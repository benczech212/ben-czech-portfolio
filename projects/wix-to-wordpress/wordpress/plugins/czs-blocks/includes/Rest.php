<?php
/**
 * REST additions for the build tooling (application-password auth only).
 * Elementor 4 already exposes _elementor_data, _elementor_edit_mode,
 * _elementor_template_type and _elementor_page_settings; we add
 * _elementor_version and the attachment's Wix media id.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks;

defined( 'ABSPATH' ) || exit;

final class Rest {

	public static function register(): void {
		add_action( 'init', array( self::class, 'register_meta' ), 20 );
	}

	public static function register_meta(): void {
		$can_edit = static function ( $allowed, $meta_key, $object_id ): bool {
			return current_user_can( 'edit_post', (int) $object_id );
		};

		foreach ( array( 'page', 'post' ) as $post_type ) {
			register_post_meta(
				$post_type,
				'_elementor_version',
				array(
					'type'              => 'string',
					'single'            => true,
					'show_in_rest'      => array( 'schema' => array( 'context' => array( 'edit' ) ) ),
					'auth_callback'     => $can_edit,
					'sanitize_callback' => static fn( $v ) => preg_replace( '/[^0-9A-Za-z.\-]/', '', (string) $v ),
				)
			);
		}

		// Wix media id on attachments, so migrations can re-find an upload (README §4: _czs_wix_id).
		register_post_meta(
			'attachment',
			'_czs_wix_id',
			array(
				'type'              => 'string',
				'single'            => true,
				'show_in_rest'      => array( 'schema' => array( 'context' => array( 'view', 'edit' ) ) ),
				'auth_callback'     => $can_edit,
				'sanitize_callback' => static fn( $v ) => preg_replace( '/[^0-9A-Za-z_~.\-]/', '', (string) $v ),
			)
		);
	}
}
