<?php
/**
 * Shared bio records ("People"): one record per person, reused by the People
 * widget on any page. Name = title, bio = content, photo = featured image,
 * role, profile link and social links = post meta. The record's slug (e.g.
 * "ben", "jane") is the stable key pages refer to.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks;

defined( 'ABSPATH' ) || exit;

final class People {

	public const POST_TYPE = 'czs_person';
	private const NONCE    = 'czs_person_meta';

	/** Meta key => [label, type, help]. */
	public static function fields(): array {
		return array(
			'_czs_person_role'          => array( __( 'Role / title (one line per row)', 'czs-blocks' ), 'textarea', __( 'Shown under the name, e.g. "Technical Artist & Systems Engineer".', 'czs-blocks' ) ),
			'_czs_person_profile_label' => array( __( 'Profile link text', 'czs-blocks' ), 'text', __( 'e.g. "Learn more about Ben". Leave empty to hide the link.', 'czs-blocks' ) ),
			'_czs_person_profile_url'   => array( __( 'Profile link address', 'czs-blocks' ), 'url', __( 'e.g. /ben-czech', 'czs-blocks' ) ),
			'_czs_person_instagram'     => array( __( 'Instagram address', 'czs-blocks' ), 'url', '' ),
			'_czs_person_facebook'      => array( __( 'Facebook address', 'czs-blocks' ), 'url', '' ),
			'_czs_person_pinterest'     => array( __( 'Pinterest address', 'czs-blocks' ), 'url', '' ),
			'_czs_person_website'       => array( __( 'Website address', 'czs-blocks' ), 'url', '' ),
		);
	}

	public static function register(): void {
		add_action( 'init', array( self::class, 'register_type' ) );
		add_action( 'add_meta_boxes_' . self::POST_TYPE, array( self::class, 'add_meta_box' ) );
		add_action( 'save_post_' . self::POST_TYPE, array( self::class, 'save_meta_box' ), 10, 2 );
		add_filter( 'use_block_editor_for_post_type', array( self::class, 'classic_editor' ), 10, 2 );
	}

	public static function register_type(): void {
		register_post_type(
			self::POST_TYPE,
			array(
				'labels'              => array(
					'name'               => __( 'People', 'czs-blocks' ),
					'singular_name'      => __( 'Person', 'czs-blocks' ),
					'menu_name'          => __( 'People (bios)', 'czs-blocks' ),
					'add_new_item'       => __( 'Add person', 'czs-blocks' ),
					'edit_item'          => __( 'Edit person', 'czs-blocks' ),
					'featured_image'     => __( 'Photo', 'czs-blocks' ),
					'set_featured_image' => __( 'Set photo', 'czs-blocks' ),
				),
				'description'         => __( 'Team bios shared by the People widget.', 'czs-blocks' ),
				'public'              => false,
				'show_ui'             => true,
				'show_in_menu'        => true,
				'menu_icon'           => 'dashicons-groups',
				'menu_position'       => 21,
				'show_in_rest'        => true,
				'rest_base'           => 'czs-people',
				'supports'            => array( 'title', 'editor', 'thumbnail', 'page-attributes', 'custom-fields' ),
				'capability_type'     => 'page',
				'map_meta_cap'        => true,
				'exclude_from_search' => true,
				'has_archive'         => false,
				'rewrite'             => false,
				'query_var'           => false,
			)
		);

		foreach ( self::fields() as $key => [ , $type ] ) {
			register_post_meta(
				self::POST_TYPE,
				$key,
				array(
					'type'              => 'string',
					'single'            => true,
					'default'           => '',
					'show_in_rest'      => true,
					'auth_callback'     => static fn( $allowed, $meta_key, $post_id ) => current_user_can( 'edit_post', (int) $post_id ),
					'sanitize_callback' => self::sanitizer( $type ),
				)
			);
		}
	}

	private static function sanitizer( string $type ): callable {
		return match ( $type ) {
			'url'      => static fn( $v ) => esc_url_raw( trim( (string) $v ) ),
			'textarea' => static fn( $v ) => sanitize_textarea_field( (string) $v ),
			default    => static fn( $v ) => sanitize_text_field( (string) $v ),
		};
	}

	/**
	 * @param bool   $use       Use block editor.
	 * @param string $post_type Post type.
	 */
	public static function classic_editor( $use, $post_type ): bool {
		return self::POST_TYPE === $post_type ? false : (bool) $use;
	}

	public static function add_meta_box(): void {
		add_meta_box( 'czs-person-details', __( 'Role and links', 'czs-blocks' ), array( self::class, 'render_meta_box' ), self::POST_TYPE, 'normal', 'high' );
	}

	public static function render_meta_box( \WP_Post $post ): void {
		wp_nonce_field( self::NONCE, self::NONCE . '_nonce' );
		echo '<p>' . esc_html__( 'The bio text goes in the main editor above; the photo is the "Photo" box on the right. The slug (e.g. "jane") is what pages use to show this person.', 'czs-blocks' ) . '</p>';
		echo '<table class="form-table" role="presentation"><tbody>';
		foreach ( self::fields() as $key => [ $label, $type, $help ] ) {
			$value = (string) get_post_meta( $post->ID, $key, true );
			$id    = 'czs-' . sanitize_html_class( $key );
			echo '<tr><th scope="row"><label for="' . esc_attr( $id ) . '">' . esc_html( $label ) . '</label></th><td>';
			if ( 'textarea' === $type ) {
				echo '<textarea class="large-text" rows="2" id="' . esc_attr( $id ) . '" name="' . esc_attr( $key ) . '">' . esc_textarea( $value ) . '</textarea>';
			} else {
				echo '<input class="regular-text" type="' . ( 'url' === $type ? 'url' : 'text' ) . '" id="' . esc_attr( $id ) . '" name="' . esc_attr( $key ) . '" value="' . esc_attr( $value ) . '">';
			}
			if ( '' !== $help ) {
				echo '<p class="description">' . esc_html( $help ) . '</p>';
			}
			echo '</td></tr>';
		}
		echo '</tbody></table>';
	}

	public static function save_meta_box( int $post_id, \WP_Post $post ): void {
		if ( defined( 'DOING_AUTOSAVE' ) && DOING_AUTOSAVE ) {
			return;
		}
		$nonce = isset( $_POST[ self::NONCE . '_nonce' ] ) ? sanitize_text_field( wp_unslash( $_POST[ self::NONCE . '_nonce' ] ) ) : '';
		if ( ! wp_verify_nonce( $nonce, self::NONCE ) || ! current_user_can( 'edit_post', $post_id ) ) {
			return;
		}
		foreach ( self::fields() as $key => [ , $type ] ) {
			if ( ! isset( $_POST[ $key ] ) ) {
				continue;
			}
			$clean = ( self::sanitizer( $type ) )( wp_unslash( $_POST[ $key ] ) ); // phpcs:ignore WordPress.Security.ValidatedSanitizedInput.InputNotSanitized -- sanitized by the per-field callback.
			update_post_meta( $post_id, $key, $clean );
		}
	}

	/**
	 * Published or draft people records by slug, in the order given.
	 *
	 * @param string[] $slugs Record slugs.
	 * @return \WP_Post[]
	 */
	public static function get_by_slugs( array $slugs ): array {
		$slugs = array_values( array_filter( array_map( 'sanitize_title', $slugs ) ) );
		if ( ! $slugs ) {
			return array();
		}
		$posts = get_posts(
			array(
				'post_type'              => self::POST_TYPE,
				'post_status'            => 'publish',
				'post_name__in'          => $slugs,
				'posts_per_page'         => count( $slugs ),
				'orderby'                => 'post_name__in',
				'no_found_rows'          => true,
				'update_post_term_cache' => false,
			)
		);
		return $posts;
	}

	/**
	 * Slug => name, for the widget's picker.
	 *
	 * @return array<string,string>
	 */
	public static function options(): array {
		static $options = null;
		if ( null === $options ) {
			$options = array();
			$posts   = get_posts(
				array(
					'post_type'              => self::POST_TYPE,
					'post_status'            => 'publish',
					'posts_per_page'         => 100,
					'orderby'                => array( 'menu_order' => 'ASC', 'title' => 'ASC' ),
					'no_found_rows'          => true,
					'update_post_meta_cache' => false,
					'update_post_term_cache' => false,
				)
			);
			foreach ( $posts as $p ) {
				$options[ $p->post_name ] = $p->post_title;
			}
		}
		return $options;
	}
}
