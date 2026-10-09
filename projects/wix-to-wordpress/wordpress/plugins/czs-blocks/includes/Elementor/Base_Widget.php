<?php
/**
 * Shared base for Czech Studios widgets: category, stylesheet dependency,
 * the "Color scheme" control, button fields, and small escaping helpers.
 * Every widget renders plain server-side HTML (no JS templates, no build).
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor;

use Elementor\Controls_Manager;
use Elementor\Widget_Base;

defined( 'ABSPATH' ) || exit;

abstract class Base_Widget extends Widget_Base {

	/** Scheme key => editor label. Colors come from Site Settings (see assets/css). */
	protected const SCHEMES = array(
		'light'  => 'White',
		'tint'   => 'Lavender mist',
		'dark'   => 'Deep purple',
		'navy'   => 'Navy',
		'forest' => 'Forest green',
	);

	protected const DEFAULT_SCHEME = 'light';

	public function get_categories(): array {
		return array( Module::CATEGORY );
	}

	public function get_style_depends(): array {
		return array( Module::STYLE );
	}

	public function has_widget_inner_wrapper(): bool {
		return false;
	}

	public function get_keywords(): array {
		return array( 'czech', 'studios', 'czs' );
	}

	/* ---------------------------------------------------------------------
	 * Controls
	 * ------------------------------------------------------------------- */

	/** "Look" section on the Style tab: one color scheme picker. */
	protected function add_look_section( ?string $default = null ): void {
		$this->start_controls_section(
			'section_look',
			array(
				'label' => esc_html__( 'Look', 'czs-blocks' ),
				'tab'   => Controls_Manager::TAB_STYLE,
			)
		);
		$this->add_control(
			'scheme',
			array(
				'label'       => esc_html__( 'Color scheme', 'czs-blocks' ),
				'type'        => Controls_Manager::SELECT,
				'options'     => array_map( 'esc_html', static::SCHEMES ),
				'default'     => $default ?? static::DEFAULT_SCHEME,
				'description' => esc_html__( 'Brand colors come from Site Settings > Global Colors.', 'czs-blocks' ),
			)
		);
		$this->end_controls_section();
	}

	protected function add_heading_controls( string $label, string $default_tag = 'h2', array $tags = array( 'h1', 'h2', 'h3' ) ): void {
		$this->add_control(
			'heading',
			array(
				'label'       => $label,
				'type'        => Controls_Manager::TEXTAREA,
				'rows'        => 2,
				'label_block' => true,
			)
		);
		$this->add_control(
			'heading_tag',
			array(
				'label'       => esc_html__( 'Heading level', 'czs-blocks' ),
				'type'        => Controls_Manager::SELECT,
				'options'     => array_combine( $tags, array_map( 'strtoupper', $tags ) ),
				'default'     => $default_tag,
				'description' => esc_html__( 'Use H1 only once per page, for the main page heading.', 'czs-blocks' ),
			)
		);
	}

	/** Two fixed buttons: "{prefix}_text" / "{prefix}_link" and "{prefix}2_...". */
	protected function add_button_controls( string $prefix = 'button', bool $second = true ): void {
		$pairs = array( $prefix => esc_html__( 'Main button', 'czs-blocks' ) );
		if ( $second ) {
			$pairs[ $prefix . '2' ] = esc_html__( 'Second button', 'czs-blocks' );
		}
		foreach ( $pairs as $key => $label ) {
			$this->add_control(
				$key . '_heading',
				array(
					'label'     => $label,
					'type'      => Controls_Manager::HEADING,
					'separator' => 'before',
				)
			);
			$this->add_control(
				$key . '_text',
				array(
					'label'       => esc_html__( 'Button text', 'czs-blocks' ),
					'type'        => Controls_Manager::TEXT,
					'description' => esc_html__( 'Leave empty to hide the button.', 'czs-blocks' ),
				)
			);
			$this->add_control(
				$key . '_link',
				array(
					'label'       => esc_html__( 'Button link', 'czs-blocks' ),
					'type'        => Controls_Manager::URL,
					'options'     => array( 'url', 'is_external', 'nofollow' ),
					'placeholder' => '/contact',
				)
			);
		}
	}

	/* ---------------------------------------------------------------------
	 * Rendering helpers (all output escaped here)
	 * ------------------------------------------------------------------- */

	protected function scheme( array $s ): string {
		$scheme = (string) ( $s['scheme'] ?? '' );
		return array_key_exists( $scheme, static::SCHEMES ) ? $scheme : static::DEFAULT_SCHEME;
	}

	protected function tag( ?string $tag, array $allowed = array( 'h1', 'h2', 'h3', 'h4' ), string $fallback = 'h2' ): string {
		$tag = strtolower( (string) $tag );
		return in_array( $tag, $allowed, true ) ? $tag : $fallback;
	}

	protected function title_id(): string {
		return 'czs-' . $this->get_id() . '-title';
	}

	/** Opening tag for the widget's full-width band. */
	protected function open_section( string $block, array $s, array $extra_classes = array(), bool $labelled = true ): void {
		$classes = array_merge( array( 'czs', 'czs-' . $block, 'czs-scheme--' . $this->scheme( $s ) ), $extra_classes );
		printf(
			'<section class="%s"%s>',
			esc_attr( implode( ' ', array_map( 'sanitize_html_class', $classes ) ) ),
			$labelled && ! empty( $s['heading'] ) ? ' aria-labelledby="' . esc_attr( $this->title_id() ) . '"' : ''
		);
	}

	protected function heading( array $s, string $class, string $default_tag = 'h2' ): void {
		if ( empty( $s['heading'] ) ) {
			return;
		}
		$tag = $this->tag( $s['heading_tag'] ?? $default_tag, array( 'h1', 'h2', 'h3', 'h4' ), $default_tag );
		printf(
			'<%1$s id="%2$s" class="%3$s">%4$s</%1$s>',
			tag_escape( $tag ),
			esc_attr( $this->title_id() ),
			esc_attr( $class ),
			nl2br( esc_html( (string) $s['heading'] ) ) // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped before nl2br.
		);
	}

	protected function eyebrow( array $s, string $class ): void {
		if ( ! empty( $s['eyebrow'] ) ) {
			printf( '<p class="%s">%s</p>', esc_attr( $class ), esc_html( (string) $s['eyebrow'] ) );
		}
	}

	/** Plain textarea -> paragraphs. Blank line = new paragraph, single newline = line break. */
	protected function paragraphs( ?string $text, string $class = '' ): string {
		$text = trim( str_replace( "\r\n", "\n", (string) $text ) );
		if ( '' === $text ) {
			return '';
		}
		$out = '';
		foreach ( preg_split( "/\n\s*\n/", $text ) as $para ) {
			$out .= sprintf(
				'<p%s>%s</p>',
				'' !== $class ? ' class="' . esc_attr( $class ) . '"' : '',
				nl2br( esc_html( trim( $para ) ) )
			);
		}
		return $out;
	}

	/** Rich text from a WYSIWYG field (admin-authored): allowed post HTML only. */
	protected function rich( ?string $html ): string {
		$html = trim( (string) $html );
		return '' === $html ? '' : wp_kses_post( wpautop( $html ) );
	}

	/**
	 * href/target/rel attributes for an Elementor URL control value.
	 *
	 * @param mixed $link URL control value.
	 */
	protected function link_attrs( $link ): string {
		$url = is_array( $link ) ? (string) ( $link['url'] ?? '' ) : (string) $link;
		if ( '' === trim( $url ) ) {
			return '';
		}
		$attrs = ' href="' . esc_url( $url ) . '"';
		$rel   = array();
		if ( is_array( $link ) && ! empty( $link['is_external'] ) ) {
			$attrs .= ' target="_blank"';
			$rel[]  = 'noopener';
		}
		if ( is_array( $link ) && ! empty( $link['nofollow'] ) ) {
			$rel[] = 'nofollow';
		}
		if ( $rel ) {
			$attrs .= ' rel="' . esc_attr( implode( ' ', $rel ) ) . '"';
		}
		return $attrs;
	}

	/** @param mixed $link URL control value. */
	protected function has_link( $link ): bool {
		return '' !== trim( is_array( $link ) ? (string) ( $link['url'] ?? '' ) : (string) $link );
	}

	/** Render up to two buttons from "{prefix}_text/_link" fields. */
	protected function buttons( array $s, string $prefix = 'button' ): void {
		$html = '';
		foreach ( array( $prefix => 'primary', $prefix . '2' => 'secondary' ) as $key => $variant ) {
			$text = trim( (string) ( $s[ $key . '_text' ] ?? '' ) );
			$link = $s[ $key . '_link' ] ?? array();
			if ( '' === $text || ! $this->has_link( $link ) ) {
				continue;
			}
			$external = is_array( $link ) && ! empty( $link['is_external'] );
			$html    .= sprintf(
				'<a class="czs-btn czs-btn--%s"%s>%s%s</a>',
				esc_attr( $variant ),
				$this->link_attrs( $link ), // Escaped in link_attrs().
				esc_html( $text ),
				$external ? '<span class="screen-reader-text"> ' . esc_html__( '(opens in a new tab)', 'czs-blocks' ) . '</span>' : ''
			);
		}
		if ( '' !== $html ) {
			echo '<div class="czs-actions">' . $html . '</div>'; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- built from escaped parts above.
		}
	}

	/**
	 * <img> for a MEDIA control value. Uses the library attachment (srcset, alt
	 * text from the Media Library) when there is one.
	 *
	 * @param mixed $media MEDIA control value.
	 */
	protected function image( $media, string $size, string $class, string $sizes = '', bool $eager = false ): string {
		$id  = is_array( $media ) ? absint( $media['id'] ?? 0 ) : 0;
		$url = is_array( $media ) ? (string) ( $media['url'] ?? '' ) : '';
		$attr = array( 'class' => $class );
		if ( $eager ) {
			$attr['loading']       = 'eager';
			$attr['fetchpriority'] = 'high';
			$attr['decoding']      = 'async';
		}
		if ( '' !== $sizes ) {
			$attr['sizes'] = $sizes;
		}
		if ( $id && wp_attachment_is_image( $id ) ) {
			return (string) wp_get_attachment_image( $id, $size, false, $attr );
		}
		if ( '' === $url || str_contains( $url, 'placeholder.png' ) ) {
			return '';
		}
		return sprintf(
			'<img class="%s" src="%s" alt="%s"%s>',
			esc_attr( $class ),
			esc_url( $url ),
			esc_attr( is_array( $media ) ? (string) ( $media['alt'] ?? '' ) : '' ),
			$eager ? ' loading="eager" fetchpriority="high"' : ' loading="lazy" decoding="async"'
		);
	}

	/** @param mixed $media MEDIA control value. */
	protected function has_image( $media ): bool {
		if ( ! is_array( $media ) ) {
			return false;
		}
		if ( ! empty( $media['id'] ) ) {
			return true;
		}
		$url = (string) ( $media['url'] ?? '' );
		return '' !== $url && ! str_contains( $url, 'placeholder.png' );
	}

	/** Empty-state hint shown only inside the Elementor editor. */
	protected function editor_hint( string $message ): void {
		if ( \Elementor\Plugin::$instance->editor->is_edit_mode() ) {
			printf( '<div class="czs-editor-hint">%s</div>', esc_html( $message ) );
		}
	}
}
