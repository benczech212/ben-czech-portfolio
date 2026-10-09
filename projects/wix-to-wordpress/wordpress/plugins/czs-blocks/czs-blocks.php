<?php
/**
 * Plugin Name:       Czech Studios Blocks
 * Description:       Czech Studios page widgets for Elementor (Hero, Image + text split, Card grid, Offering card, People, Call-to-action band, Quote, Latest posts, Event list), shared team bios, WebP image output and admin-only sanitized SVG uploads.
 * Version:           0.1.0
 * Requires at least: 6.5
 * Requires PHP:      8.3
 * Author:            Czech Studios
 * License:           GPL-2.0-or-later
 * Text Domain:       czs-blocks
 *
 * @package CzechStudios\Blocks
 */

defined( 'ABSPATH' ) || exit;

define( 'CZS_BLOCKS_VERSION', '0.1.0' );
define( 'CZS_BLOCKS_FILE', __FILE__ );
define( 'CZS_BLOCKS_DIR', plugin_dir_path( __FILE__ ) );
define( 'CZS_BLOCKS_URL', plugin_dir_url( __FILE__ ) );

/*
 * Tiny PSR-4 autoloader (no Composer at runtime, see docs/plugins/README.md §4):
 * CzechStudios\Blocks\Elementor\Widgets\Card_Grid -> includes/Elementor/Widgets/Card_Grid.php
 */
spl_autoload_register(
	static function ( string $class_name ): void {
		$prefix = 'CzechStudios\\Blocks\\';
		if ( ! str_starts_with( $class_name, $prefix ) ) {
			return;
		}
		$relative = substr( $class_name, strlen( $prefix ) );
		$file     = CZS_BLOCKS_DIR . 'includes/' . str_replace( '\\', '/', $relative ) . '.php';
		if ( is_readable( $file ) ) {
			require_once $file;
		}
	}
);

add_action( 'plugins_loaded', array( \CzechStudios\Blocks\Plugin::class, 'boot' ) );
