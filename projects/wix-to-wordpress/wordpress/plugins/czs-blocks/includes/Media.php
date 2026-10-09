<?php
/**
 * Media rules:
 *  - JPEG/PNG uploads get WebP sub-sizes (image_editor_output_format), quality 82.
 *  - SVG uploads are allowed for administrators only, and every SVG is sanitized
 *    before WordPress stores it.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks;

defined( 'ABSPATH' ) || exit;

final class Media {

	public const WEBP_QUALITY = 82;

	public static function register(): void {
		add_filter( 'image_editor_output_format', array( self::class, 'webp_output' ) );
		add_filter( 'wp_editor_set_quality', array( self::class, 'webp_quality' ), 10, 2 );

		add_filter( 'upload_mimes', array( self::class, 'svg_mime' ) );
		add_filter( 'wp_check_filetype_and_ext', array( self::class, 'svg_filetype' ), 10, 4 );
		add_filter( 'wp_handle_upload_prefilter', array( self::class, 'svg_sanitize_upload' ) );
		add_filter( 'wp_prepare_attachment_for_js', array( self::class, 'svg_media_library_preview' ), 10, 2 );
	}

	/**
	 * Map JPEG and PNG output to WebP. Filterable so it can be switched off:
	 * add_filter( 'czs/blocks/webp_output', '__return_false' ).
	 *
	 * @param array<string,string> $formats Mime => output mime.
	 * @return array<string,string>
	 */
	public static function webp_output( $formats ): array {
		$formats = is_array( $formats ) ? $formats : array();
		if ( ! apply_filters( 'czs/blocks/webp_output', true ) ) {
			return $formats;
		}
		if ( ! wp_image_editor_supports( array( 'mime_type' => 'image/webp' ) ) ) {
			return $formats;
		}
		$formats['image/jpeg'] = 'image/webp';
		$formats['image/png']  = 'image/webp';
		return $formats;
	}

	/**
	 * @param int    $quality   Default quality.
	 * @param string $mime_type Output mime.
	 */
	public static function webp_quality( $quality, $mime_type = '' ): int {
		return 'image/webp' === $mime_type ? self::WEBP_QUALITY : (int) $quality;
	}

	/** Capability check; filterable, defaults to site administrators. */
	public static function user_can_upload_svg(): bool {
		return (bool) apply_filters( 'czs/blocks/can_upload_svg', current_user_can( 'manage_options' ) );
	}

	/**
	 * @param array<string,string> $mimes Allowed mimes.
	 * @return array<string,string>
	 */
	public static function svg_mime( $mimes ): array {
		$mimes = is_array( $mimes ) ? $mimes : array();
		if ( self::user_can_upload_svg() ) {
			$mimes['svg'] = 'image/svg+xml';
		} else {
			unset( $mimes['svg'], $mimes['svgz'] );
		}
		return $mimes;
	}

	/**
	 * WordPress's content sniffing can report SVG as text/plain or text/html; accept
	 * it only for .svg files from users allowed to upload SVG.
	 *
	 * @param array<string,mixed> $data     Result.
	 * @param string              $file     Temp path.
	 * @param string              $filename Original name.
	 * @param array|null          $mimes    Allowed mimes.
	 * @return array<string,mixed>
	 */
	public static function svg_filetype( $data, $file, $filename, $mimes ): array {
		$data = is_array( $data ) ? $data : array();
		if ( ! empty( $data['type'] ) ) {
			return $data;
		}
		if ( 'svg' === strtolower( pathinfo( (string) $filename, PATHINFO_EXTENSION ) ) && self::user_can_upload_svg() ) {
			$data['ext']  = 'svg';
			$data['type'] = 'image/svg+xml';
		}
		return $data;
	}

	/**
	 * Sanitize SVGs in place before WordPress moves them into uploads.
	 *
	 * @param array<string,mixed> $file Upload array.
	 * @return array<string,mixed>
	 */
	public static function svg_sanitize_upload( $file ): array {
		$file = is_array( $file ) ? $file : array();
		$name = (string) ( $file['name'] ?? '' );
		$ext  = strtolower( pathinfo( $name, PATHINFO_EXTENSION ) );
		if ( 'svg' !== $ext && 'svgz' !== $ext ) {
			return $file;
		}
		if ( 'svgz' === $ext || ! self::user_can_upload_svg() ) {
			$file['error'] = __( 'SVG uploads are limited to administrators (compressed .svgz is not accepted).', 'czs-blocks' );
			return $file;
		}
		$tmp   = (string) ( $file['tmp_name'] ?? '' );
		$raw   = ( '' !== $tmp && is_readable( $tmp ) ) ? file_get_contents( $tmp ) : false; // phpcs:ignore WordPress.WP.AlternativeFunctions.file_get_contents_file_get_contents
		$clean = false === $raw ? null : Svg_Sanitizer::sanitize( $raw );
		if ( null === $clean ) {
			$file['error'] = __( 'This SVG could not be safely cleaned, so it was not uploaded.', 'czs-blocks' );
			return $file;
		}
		file_put_contents( $tmp, $clean ); // phpcs:ignore WordPress.WP.AlternativeFunctions.file_system_operations_file_put_contents
		$file['size'] = strlen( $clean );
		return $file;
	}

	/**
	 * Give SVGs a preview in the media library grid.
	 *
	 * @param array<string,mixed> $response Attachment data for JS.
	 * @param \WP_Post            $attachment Attachment.
	 * @return array<string,mixed>
	 */
	public static function svg_media_library_preview( $response, $attachment ): array {
		$response = is_array( $response ) ? $response : array();
		if ( 'image/svg+xml' === ( $response['mime'] ?? '' ) && empty( $response['sizes'] ) ) {
			$url               = (string) wp_get_attachment_url( $attachment->ID );
			$response['image'] = array( 'src' => $url );
			$response['sizes'] = array(
				'full' => array(
					'url'         => $url,
					'orientation' => 'landscape',
				),
			);
		}
		return $response;
	}
}
