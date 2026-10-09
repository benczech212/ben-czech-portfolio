<?php
/**
 * Quote: testimonials and pull quotes.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;

defined( 'ABSPATH' ) || exit;

class Quote extends Base_Widget {

	protected const DEFAULT_SCHEME = 'tint';

	public function get_name(): string {
		return 'czs-quote';
	}

	public function get_title(): string {
		return esc_html__( 'CS Quote', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-blockquote';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'quote', 'testimonial', 'review' ) );
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Quote', 'czs-blocks' ) ) );
		$this->add_control(
			'quote',
			array(
				'label' => esc_html__( 'Quote', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXTAREA,
				'rows'  => 5,
			)
		);
		$this->add_control(
			'name',
			array(
				'label' => esc_html__( 'Who said it', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXT,
			)
		);
		$this->add_control(
			'role',
			array(
				'label' => esc_html__( 'Their role or context (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXT,
			)
		);
		$this->add_control(
			'image',
			array(
				'label' => esc_html__( 'Photo (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::MEDIA,
			)
		);
		$this->end_controls_section();
		$this->add_look_section();
	}

	protected function render(): void {
		$s = $this->get_settings_for_display();
		if ( empty( $s['quote'] ) ) {
			$this->editor_hint( __( 'Type the quote in the Content tab.', 'czs-blocks' ) );
			return;
		}
		$this->open_section( 'quote', $s, array(), false );
		echo '<div class="czs-inner"><figure class="czs-quote__figure">';
		echo '<blockquote class="czs-quote__text">' . $this->paragraphs( $s['quote'] ) . '</blockquote>'; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
		if ( ! empty( $s['name'] ) ) {
			echo '<figcaption class="czs-quote__by">';
			if ( $this->has_image( $s['image'] ?? null ) ) {
				echo $this->image( $s['image'], 'thumbnail', 'czs-quote__img' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in image().
			}
			echo '<span class="czs-quote__name">' . esc_html( (string) $s['name'] ) . '</span>';
			if ( ! empty( $s['role'] ) ) {
				echo '<span class="czs-quote__role">' . esc_html( (string) $s['role'] ) . '</span>';
			}
			echo '</figcaption>';
		}
		echo '</figure></div></section>';
	}
}
