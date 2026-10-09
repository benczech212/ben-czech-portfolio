<?php
/**
 * Image + text split: story blocks, bios, "how we work".
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;

defined( 'ABSPATH' ) || exit;

class Split extends Base_Widget {

	public function get_name(): string {
		return 'czs-split';
	}

	public function get_title(): string {
		return esc_html__( 'CS Image + Text', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-image-box';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'split', 'image', 'text', 'story', 'about' ) );
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Image + text', 'czs-blocks' ) ) );

		$this->add_control(
			'eyebrow',
			array(
				'label'       => esc_html__( 'Small label above the heading', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXT,
				'label_block' => true,
			)
		);
		$this->add_heading_controls( esc_html__( 'Heading', 'czs-blocks' ), 'h2' );
		$this->add_control(
			'body',
			array(
				'label' => esc_html__( 'Text', 'czs-blocks' ),
				'type'  => Controls_Manager::WYSIWYG,
			)
		);
		$this->add_control(
			'image',
			array(
				'label'       => esc_html__( 'Image', 'czs-blocks' ),
				'type'        => Controls_Manager::MEDIA,
				'description' => esc_html__( 'Optional. Without an image the text is centered.', 'czs-blocks' ),
			)
		);
		$this->add_control(
			'image_side',
			array(
				'label'   => esc_html__( 'Image side', 'czs-blocks' ),
				'type'    => Controls_Manager::CHOOSE,
				'options' => array(
					'left'  => array(
						'title' => esc_html__( 'Left', 'czs-blocks' ),
						'icon'  => 'eicon-h-align-left',
					),
					'right' => array(
						'title' => esc_html__( 'Right', 'czs-blocks' ),
						'icon'  => 'eicon-h-align-right',
					),
				),
				'default' => 'left',
				'toggle'  => false,
			)
		);
		$this->add_control(
			'image_shape',
			array(
				'label'   => esc_html__( 'Image shape', 'czs-blocks' ),
				'type'    => Controls_Manager::SELECT,
				'options' => array(
					'rounded' => esc_html__( 'Rounded corners', 'czs-blocks' ),
					'arch'    => esc_html__( 'Arch', 'czs-blocks' ),
					'circle'  => esc_html__( 'Circle', 'czs-blocks' ),
					'natural' => esc_html__( 'As uploaded (no crop)', 'czs-blocks' ),
				),
				'default' => 'rounded',
			)
		);
		$this->add_button_controls();
		$this->end_controls_section();

		$this->add_look_section();
	}

	protected function render(): void {
		$s         = $this->get_settings_for_display();
		$has_image = $this->has_image( $s['image'] ?? null );
		$side      = 'right' === ( $s['image_side'] ?? '' ) ? 'right' : 'left';
		$shape     = in_array( $s['image_shape'] ?? '', array( 'rounded', 'arch', 'circle', 'natural' ), true ) ? $s['image_shape'] : 'rounded';

		$this->open_section( 'split', $s, array( $has_image ? 'czs-split--image-' . $side : 'czs-split--text-only' ) );
		echo '<div class="czs-inner czs-split__grid">';
		if ( $has_image ) {
			echo '<div class="czs-split__media czs-shape--' . esc_attr( $shape ) . '">';
			echo $this->image( $s['image'], 'large', 'czs-split__img', '(min-width: 900px) 45vw, 100vw' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in image().
			echo '</div>';
		}
		echo '<div class="czs-split__text">';
		$this->eyebrow( $s, 'czs-eyebrow' );
		$this->heading( $s, 'czs-title', 'h2' );
		echo '<div class="czs-prose">' . $this->rich( $s['body'] ?? '' ) . '</div>'; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- wp_kses_post in rich().
		$this->buttons( $s );
		echo '</div></div></section>';
	}
}
