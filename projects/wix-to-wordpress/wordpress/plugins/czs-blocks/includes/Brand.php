<?php
/**
 * Brand palette and fonts, in one place. Used to seed Elementor's Site Settings
 * (the active kit). The widget CSS reads the kit's CSS variables and only falls
 * back to these hex values if the kit is missing.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks;

defined( 'ABSPATH' ) || exit;

final class Brand {

	public const HEADING_FONT = 'Playfair Display';
	public const BODY_FONT    = 'Source Sans 3';

	/**
	 * Elementor system colors. Elementor's meaning: Primary = headings,
	 * Secondary = decorative accents, Text = body copy, Accent = buttons and links.
	 * Accent is plum (not gold) so white button text passes WCAG AA.
	 *
	 * @return array<int, array{_id:string,title:string,color:string}>
	 */
	public static function system_colors(): array {
		return array(
			array( '_id' => 'primary', 'title' => 'Deep purple (headings)', 'color' => '#502E59' ),
			array( '_id' => 'secondary', 'title' => 'Gold (decorative)', 'color' => '#BF8A03' ),
			array( '_id' => 'text', 'title' => 'Navy (body text)', 'color' => '#2F4159' ),
			array( '_id' => 'accent', 'title' => 'Plum (buttons, links)', 'color' => '#834575' ),
		);
	}

	/**
	 * Extra brand colors. The `czs*` ids are stable: widget CSS reads
	 * --e-global-color-{id}. The site owner can add their own colors next to them.
	 *
	 * @return array<int, array{_id:string,title:string,color:string}>
	 */
	public static function custom_colors(): array {
		return array(
			array( '_id' => 'czsnavy', 'title' => 'Navy', 'color' => '#2F4159' ),
			array( '_id' => 'czsforest', 'title' => 'Forest', 'color' => '#01402E' ),
			array( '_id' => 'czssky', 'title' => 'Sky', 'color' => '#3ABEFE' ),
			array( '_id' => 'czsrust', 'title' => 'Rust', 'color' => '#974127' ),
			array( '_id' => 'czsyellow', 'title' => 'Sunflower', 'color' => '#FFCB05' ),
			array( '_id' => 'czsred', 'title' => 'Red', 'color' => '#ED1C24' ),
			array( '_id' => 'czsgray', 'title' => 'Light gray', 'color' => '#D5D9DE' ),
			array( '_id' => 'czsgraymid', 'title' => 'Mid gray', 'color' => '#828D9B' ),
			array( '_id' => 'czsmist', 'title' => 'Lavender mist (section tint)', 'color' => '#F6F1F7' ),
			array( '_id' => 'czswhite', 'title' => 'White', 'color' => '#FFFFFF' ),
		);
	}

	/**
	 * @return array<int, array<string, mixed>>
	 */
	public static function system_typography(): array {
		$font = static function ( string $id, string $title, string $family, string $weight ): array {
			return array(
				'_id'                    => $id,
				'title'                  => $title,
				'typography_typography'  => 'custom',
				'typography_font_family' => $family,
				'typography_font_weight' => $weight,
			);
		};
		return array(
			$font( 'primary', 'Headings (Playfair Display)', self::HEADING_FONT, '600' ),
			$font( 'secondary', 'Subheadings (Playfair Display)', self::HEADING_FONT, '400' ),
			$font( 'text', 'Body (Source Sans 3)', self::BODY_FONT, '400' ),
			$font( 'accent', 'Buttons and labels (Source Sans 3)', self::BODY_FONT, '600' ),
		);
	}

	/**
	 * Kit "Theme Style" settings so core Elementor widgets and plain content
	 * also follow the brand. Values point at the globals, so editing a global
	 * color in Site Settings updates everything.
	 *
	 * @return array<string, mixed>
	 */
	public static function theme_style(): array {
		$globals = array(
			'body_color'                  => 'globals/colors?id=text',
			'body_typography_typography'  => 'globals/typography?id=text',
			'link_normal_color'           => 'globals/colors?id=accent',
			'link_hover_color'            => 'globals/colors?id=primary',
			'button_typography_typography' => 'globals/typography?id=accent',
			'button_background_color'     => 'globals/colors?id=accent',
			'button_hover_background_color' => 'globals/colors?id=primary',
			'button_text_color'           => 'globals/colors?id=czswhite',
			'button_hover_text_color'     => 'globals/colors?id=czswhite',
		);
		foreach ( array( 'h1', 'h2', 'h3', 'h4', 'h5', 'h6' ) as $h ) {
			$globals[ $h . '_color' ]                 = 'globals/colors?id=primary';
			$globals[ $h . '_typography_typography' ] = 'globals/typography?id=primary';
		}
		return array(
			'__globals__'           => $globals,
			'default_generic_fonts' => 'Sans-serif',
			'button_border_radius'  => array(
				'unit'     => 'px',
				'top'      => '999',
				'right'    => '999',
				'bottom'   => '999',
				'left'     => '999',
				'isLinked' => true,
			),
			'button_padding'        => array(
				'unit'     => 'px',
				'top'      => '14',
				'right'    => '28',
				'bottom'   => '14',
				'left'     => '28',
				'isLinked' => false,
			),
		);
	}

	/**
	 * Merge the brand into the active Elementor kit. Idempotent: our ids are
	 * replaced, anything the site owner added (other custom colors/fonts) is kept.
	 *
	 * @return int The kit post ID.
	 */
	public static function apply_to_kit(): int {
		if ( ! class_exists( '\Elementor\Plugin' ) ) {
			throw new \RuntimeException( 'Elementor is not active.' );
		}
		$kit = \Elementor\Plugin::$instance->kits_manager->get_active_kit();
		if ( ! $kit || ! $kit->get_id() ) {
			throw new \RuntimeException( 'No active Elementor kit.' );
		}

		$current = get_post_meta( $kit->get_id(), '_elementor_page_settings', true );
		$current = is_array( $current ) ? $current : array();

		$merge_by_id = static function ( array $existing, array $ours ): array {
			$our_ids = wp_list_pluck( $ours, '_id' );
			$kept    = array_filter(
				$existing,
				static fn( $item ) => is_array( $item ) && ! in_array( $item['_id'] ?? '', $our_ids, true )
			);
			return array_values( array_merge( $ours, $kept ) );
		};

		$style = self::theme_style();

		$settings                      = $current;
		$settings['system_colors']     = self::system_colors();
		$settings['custom_colors']     = $merge_by_id( (array) ( $current['custom_colors'] ?? array() ), self::custom_colors() );
		$settings['system_typography'] = self::system_typography();
		$settings['custom_typography'] = array_values( (array) ( $current['custom_typography'] ?? array() ) );
		$settings['__globals__']       = array_merge( (array) ( $current['__globals__'] ?? array() ), $style['__globals__'] );
		unset( $style['__globals__'] );
		$settings = array_merge( $settings, $style );

		// Document API saves the meta the way the editor does.
		$kit->update_settings( $settings );
		\Elementor\Plugin::$instance->files_manager->clear_cache();

		return (int) $kit->get_id();
	}
}
