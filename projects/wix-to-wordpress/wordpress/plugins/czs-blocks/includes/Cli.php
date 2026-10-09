<?php
/**
 * WP-CLI: `wp czs-blocks apply-kit`, `wp czs-blocks status`.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks;

defined( 'ABSPATH' ) || exit;

final class Cli {

	/**
	 * Apply the Czech Studios palette and fonts to Elementor's active kit (Site Settings). Safe to re-run.
	 *
	 * @subcommand apply-kit
	 */
	public function apply_kit(): void {
		try {
			$kit_id = Brand::apply_to_kit();
		} catch ( \RuntimeException $e ) {
			\WP_CLI::error( $e->getMessage() );
			return;
		}
		\WP_CLI::success( sprintf( 'Brand colors and fonts applied to Elementor kit %d.', $kit_id ) );
	}

	/**
	 * Print plugin status: version, widgets, people records.
	 */
	public function status(): void {
		\WP_CLI::line( 'czs-blocks ' . CZS_BLOCKS_VERSION );
		\WP_CLI::line( 'Elementor: ' . ( defined( 'ELEMENTOR_VERSION' ) ? ELEMENTOR_VERSION : 'inactive' ) );
		\WP_CLI::line( 'Widgets: ' . implode( ', ', array_keys( Elementor\Module::widget_classes() ) ) );
		$people = get_posts(
			array(
				'post_type'      => People::POST_TYPE,
				'post_status'    => 'any',
				'posts_per_page' => 50,
				'no_found_rows'  => true,
			)
		);
		\WP_CLI::line( 'People: ' . implode( ', ', wp_list_pluck( $people, 'post_name' ) ) );
	}
}
