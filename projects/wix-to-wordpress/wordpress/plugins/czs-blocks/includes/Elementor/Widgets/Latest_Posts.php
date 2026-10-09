<?php
/**
 * Latest posts: a small blog feed, optionally filtered by category.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;

defined( 'ABSPATH' ) || exit;

class Latest_Posts extends Base_Widget {

	public function get_name(): string {
		return 'czs-latest-posts';
	}

	public function get_title(): string {
		return esc_html__( 'CS Latest Posts', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-posts-grid';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'blog', 'posts', 'journal', 'news' ) );
	}

	protected function is_dynamic_content(): bool {
		return true;
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Latest posts', 'czs-blocks' ) ) );
		$this->add_heading_controls( esc_html__( 'Section heading (optional)', 'czs-blocks' ), 'h2', array( 'h2', 'h3' ) );

		$categories = array( '' => esc_html__( 'All categories', 'czs-blocks' ) );
		foreach ( get_categories( array( 'hide_empty' => false, 'number' => 100 ) ) as $term ) {
			$categories[ $term->slug ] = $term->name;
		}
		$this->add_control(
			'category',
			array(
				'label'   => esc_html__( 'Category', 'czs-blocks' ),
				'type'    => Controls_Manager::SELECT,
				'options' => $categories,
				'default' => '',
			)
		);
		$this->add_control(
			'count',
			array(
				'label'   => esc_html__( 'How many posts', 'czs-blocks' ),
				'type'    => Controls_Manager::NUMBER,
				'min'     => 1,
				'max'     => 12,
				'default' => 3,
			)
		);
		$this->add_control(
			'show_excerpt',
			array(
				'label'        => esc_html__( 'Show a short excerpt', 'czs-blocks' ),
				'type'         => Controls_Manager::SWITCHER,
				'return_value' => 'yes',
				'default'      => 'yes',
			)
		);
		$this->add_button_controls( 'button', false );
		$this->end_controls_section();
		$this->add_look_section();
	}

	protected function render(): void {
		$s     = $this->get_settings_for_display();
		$count = max( 1, min( 12, absint( $s['count'] ?? 3 ) ) );
		$args  = array(
			'post_type'           => 'post',
			'post_status'         => 'publish',
			'posts_per_page'      => $count,
			'ignore_sticky_posts' => true,
			'no_found_rows'       => true,
		);
		if ( ! empty( $s['category'] ) ) {
			$args['category_name'] = sanitize_title( (string) $s['category'] );
		}
		$query = new \WP_Query( $args );

		$this->open_section( 'latest-posts', $s );
		echo '<div class="czs-inner">';
		if ( ! empty( $s['heading'] ) ) {
			echo '<header class="czs-section-head">';
			$this->heading( $s, 'czs-title', 'h2' );
			echo '</header>';
		}
		if ( ! $query->have_posts() ) {
			$this->editor_hint( __( 'No published posts in this category yet.', 'czs-blocks' ) );
		}
		$ctag = ( $s['heading_tag'] ?? 'h2' ) === 'h3' ? 'h4' : 'h3';
		echo '<ul class="czs-cards czs-cols-3" role="list">';
		foreach ( $query->posts as $post ) {
			echo '<li class="czs-card czs-card--image"><article>';
			if ( has_post_thumbnail( $post ) ) {
				echo '<div class="czs-card__media">' . get_the_post_thumbnail( $post, 'medium_large', array( 'class' => 'czs-card__img', 'sizes' => '(min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw' ) ) . '</div>';
			}
			echo '<div class="czs-card__body">';
			printf( '<p class="czs-card__meta"><time datetime="%s">%s</time></p>', esc_attr( get_the_date( 'c', $post ) ), esc_html( get_the_date( '', $post ) ) );
			printf( '<%1$s class="czs-card__title"><a href="%2$s">%3$s</a></%1$s>', tag_escape( $ctag ), esc_url( get_permalink( $post ) ), esc_html( get_the_title( $post ) ) );
			if ( 'yes' === ( $s['show_excerpt'] ?? '' ) ) {
				echo '<p class="czs-card__text">' . esc_html( wp_trim_words( get_the_excerpt( $post ), 24 ) ) . '</p>';
			}
			echo '</div></article></li>';
		}
		echo '</ul>';
		$this->buttons( $s );
		echo '</div></section>';
		wp_reset_postdata();
	}
}
