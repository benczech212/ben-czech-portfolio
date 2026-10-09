<?php
/**
 * Elementor integration: "Czech Studios" category, widget registration and the
 * shared stylesheet. The stylesheet is only registered here; Elementor enqueues
 * it on pages that actually contain one of our widgets (get_style_depends).
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor;

defined( 'ABSPATH' ) || exit;

final class Module {

	public const CATEGORY = 'czech-studios';
	public const STYLE    = 'czs-blocks';

	/** Widget name => class. */
	public static function widget_classes(): array {
		return array(
			'czs-hero'          => Widgets\Hero::class,
			'czs-split'         => Widgets\Split::class,
			'czs-card-grid'     => Widgets\Card_Grid::class,
			'czs-offering-card' => Widgets\Offering_Card::class,
			'czs-people'        => Widgets\People::class,
			'czs-cta-band'      => Widgets\Cta_Band::class,
			'czs-quote'         => Widgets\Quote::class,
			'czs-latest-posts'  => Widgets\Latest_Posts::class,
			'czs-event-list'    => Widgets\Event_List::class,
		);
	}

	public static function register(): void {
		add_action( 'elementor/elements/categories_registered', array( self::class, 'register_category' ) );
		add_action( 'elementor/widgets/register', array( self::class, 'register_widgets' ) );
		add_action( 'elementor/frontend/after_register_styles', array( self::class, 'register_styles' ) );
		add_action( 'elementor/editor/after_enqueue_styles', array( self::class, 'editor_styles' ) );
	}

	/** @param \Elementor\Elements_Manager $manager Elements manager. */
	public static function register_category( $manager ): void {
		$manager->add_category(
			self::CATEGORY,
			array(
				'title' => esc_html__( 'Czech Studios', 'czs-blocks' ),
				'icon'  => 'eicon-paint-brush',
			)
		);
	}

	/** @param \Elementor\Widgets_Manager $manager Widgets manager. */
	public static function register_widgets( $manager ): void {
		foreach ( self::widget_classes() as $class ) {
			$manager->register( new $class() );
		}
	}

	public static function register_styles(): void {
		wp_register_style( self::STYLE, CZS_BLOCKS_URL . 'assets/css/czs-blocks.css', array(), CZS_BLOCKS_VERSION );
	}

	/** Brand-colored category heading in the editor panel. */
	public static function editor_styles(): void {
		wp_add_inline_style(
			'elementor-editor',
			'#elementor-panel-category-' . self::CATEGORY . ' .elementor-panel-category-title{color:#834575}'
		);
	}
}
