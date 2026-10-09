<?php
/**
 * Bootstrap: wires each small module. Feature code loads lazily; Elementor
 * widget classes are only autoloaded when Elementor asks for widgets.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks;

defined( 'ABSPATH' ) || exit;

final class Plugin {

	public static function boot(): void {
		People::register();
		Media::register();
		Rest::register();

		// Elementor integration only when Elementor is active.
		if ( did_action( 'elementor/loaded' ) ) {
			Elementor\Module::register();
		}

		if ( defined( 'WP_CLI' ) && WP_CLI ) {
			\WP_CLI::add_command( 'czs-blocks', Cli::class );
		}
	}
}
