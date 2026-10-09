<?php
/**
 * Call-to-action band: closing prompt with one or two buttons.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;

defined( 'ABSPATH' ) || exit;

class Cta_Band extends Base_Widget {

	protected const DEFAULT_SCHEME = 'dark';

	public function get_name(): string {
		return 'czs-cta-band';
	}

	public function get_title(): string {
		return esc_html__( 'CS Call-to-Action Band', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-call-to-action';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'cta', 'call to action', 'band', 'contact' ) );
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Call to action', 'czs-blocks' ) ) );
		$this->add_heading_controls( esc_html__( 'Heading', 'czs-blocks' ), 'h2', array( 'h2', 'h3' ) );
		$this->add_control(
			'text',
			array(
				'label' => esc_html__( 'Text', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXTAREA,
				'rows'  => 3,
			)
		);
		$this->add_button_controls();
		$this->end_controls_section();

		$this->add_look_section();
	}

	protected function render(): void {
		$s = $this->get_settings_for_display();
		$this->open_section( 'cta-band', $s );
		echo '<div class="czs-inner czs-cta-band__inner">';
		echo '<span class="czs-rule" aria-hidden="true"></span>';
		$this->heading( $s, 'czs-title', 'h2' );
		echo $this->paragraphs( $s['text'] ?? '', 'czs-lead' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
		$this->buttons( $s );
		echo '</div></section>';
	}
}
