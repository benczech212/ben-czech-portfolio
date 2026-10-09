<?php
/**
 * Hero: page opener with eyebrow, heading, intro, image and up to two buttons.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;

defined( 'ABSPATH' ) || exit;

class Hero extends Base_Widget {

	protected const DEFAULT_SCHEME = 'tint';

	public function get_name(): string {
		return 'czs-hero';
	}

	public function get_title(): string {
		return esc_html__( 'CS Hero', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-header';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'hero', 'banner', 'header', 'intro' ) );
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Hero', 'czs-blocks' ) ) );

		$this->add_control(
			'eyebrow',
			array(
				'label'       => esc_html__( 'Small label above the heading', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXT,
				'label_block' => true,
			)
		);
		$this->add_heading_controls( esc_html__( 'Heading', 'czs-blocks' ), 'h1', array( 'h1', 'h2' ) );
		$this->add_control(
			'intro',
			array(
				'label'       => esc_html__( 'Intro text', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXTAREA,
				'rows'        => 6,
				'description' => esc_html__( 'Leave a blank line between paragraphs.', 'czs-blocks' ),
			)
		);
		$this->add_control(
			'image',
			array(
				'label' => esc_html__( 'Image', 'czs-blocks' ),
				'type'  => Controls_Manager::MEDIA,
			)
		);
		$this->add_control(
			'layout',
			array(
				'label'   => esc_html__( 'Image placement', 'czs-blocks' ),
				'type'    => Controls_Manager::SELECT,
				'options' => array(
					'right'      => esc_html__( 'Image on the right', 'czs-blocks' ),
					'left'       => esc_html__( 'Image on the left', 'czs-blocks' ),
					'background' => esc_html__( 'Image behind the text', 'czs-blocks' ),
				),
				'default' => 'right',
			)
		);
		$this->add_button_controls();
		$this->end_controls_section();

		$this->add_look_section();
	}

	protected function render(): void {
		$s         = $this->get_settings_for_display();
		$has_image = $this->has_image( $s['image'] ?? null );
		$layout    = in_array( $s['layout'] ?? '', array( 'left', 'right', 'background' ), true ) ? $s['layout'] : 'right';
		if ( ! $has_image ) {
			$layout = 'text';
		}
		// With "image behind the text", the color scheme picks the overlay:
		// light schemes fade the image to white, dark schemes darken it.
		$classes = array( 'czs-hero--' . $layout );

		$this->open_section( 'hero', $s, $classes );
		if ( 'background' === $layout ) {
			echo '<div class="czs-hero__bg" aria-hidden="true">';
			echo $this->image( $s['image'], 'full', 'czs-hero__bg-img', '100vw', true ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in image().
			echo '</div>';
		}
		echo '<div class="czs-inner czs-hero__grid">';
		echo '<div class="czs-hero__text">';
		$this->eyebrow( $s, 'czs-eyebrow' );
		$this->heading( $s, 'czs-title czs-hero__title', 'h1' );
		echo '<div class="czs-lead">' . $this->paragraphs( $s['intro'] ?? '' ) . '</div>'; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
		$this->buttons( $s );
		echo '</div>';
		if ( 'left' === $layout || 'right' === $layout ) {
			echo '<div class="czs-hero__media">';
			echo $this->image( $s['image'], 'large', 'czs-hero__img', '(min-width: 900px) 50vw, 100vw', true ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in image().
			echo '</div>';
		}
		echo '</div></section>';
	}
}
