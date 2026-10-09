<?php
/**
 * Uninstall: keeps data by default (docs/plugins/README.md §5.2). People
 * records are removed only if the option czs_blocks_delete_data is set.
 *
 * @package CzechStudios\Blocks
 */

defined( 'WP_UNINSTALL_PLUGIN' ) || exit;

if ( ! get_option( 'czs_blocks_delete_data' ) ) {
	return;
}

$czs_people = get_posts(
	array(
		'post_type'      => 'czs_person',
		'post_status'    => 'any',
		'posts_per_page' => -1,
		'fields'         => 'ids',
	)
);
foreach ( $czs_people as $czs_person_id ) {
	wp_delete_post( (int) $czs_person_id, true );
}
delete_option( 'czs_blocks_delete_data' );
