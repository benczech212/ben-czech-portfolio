<?php
/**
 * Offering card: one workshop or program with price, format and a button.
 * Place several side by side in an Elementor container for a package row.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;

defined( 'ABSPATH' ) || exit;

class Offering_Card extends Base_Widget {

	public function get_name(): string {
		return 'czs-offering-card';
	}

	public function get_title(): string {
		return esc_html__( 'CS Offering Card', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-price-table';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'offering', 'workshop', 'program', 'price', 'package' ) );
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Offering', 'czs-blocks' ) ) );
		$this->add_control(
			'image',
			array(
				'label' => esc_html__( 'Image (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::MEDIA,
			)
		);
		$this->add_control(
			'title',
			array(
				'label'       => esc_html__( 'Title', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXT,
				'label_block' => true,
			)
		);
		$this->add_control(
			'title_tag',
			array(
				'label'   => esc_html__( 'Title level', 'czs-blocks' ),
				'type'    => Controls_Manager::SELECT,
				'options' => array(
					'h2' => 'H2',
					'h3' => 'H3',
				),
				'default' => 'h3',
			)
		);
		$this->add_control(
			'format',
			array(
				'label'       => esc_html__( 'Format', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXT,
				'placeholder' => esc_html__( 'e.g. Online, 5 days', 'czs-blocks' ),
			)
		);
		$this->add_control(
			'blurb',
			array(
				'label' => esc_html__( 'Short description', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXTAREA,
				'rows'  => 4,
			)
		);
		$this->add_control(
			'price',
			array(
				'label'       => esc_html__( 'Price', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXT,
				'placeholder' => esc_html__( 'e.g. $45 or Free', 'czs-blocks' ),
			)
		);
		$this->add_control(
			'featured',
			array(
				'label'        => esc_html__( 'Highlight this card', 'czs-blocks' ),
				'type'         => Controls_Manager::SWITCHER,
				'return_value' => 'yes',
			)
		);
		$this->add_button_controls( 'button', false );
		$this->end_controls_section();
	}

	protected function render(): void {
		$s   = $this->get_settings_for_display();
		$tag = $this->tag( $s['title_tag'] ?? 'h3', array( 'h2', 'h3' ), 'h3' );
		printf( '<article class="czs czs-offering%s">', 'yes' === ( $s['featured'] ?? '' ) ? ' czs-offering--featured' : '' );
		if ( $this->has_image( $s['image'] ?? null ) ) {
			echo '<div class="czs-offering__media">' . $this->image( $s['image'], 'medium_large', 'czs-offering__img', '(min-width: 900px) 33vw, 100vw' ) . '</div>'; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in image().
		}
		echo '<div class="czs-offering__body">';
		if ( ! empty( $s['format'] ) ) {
			echo '<p class="czs-eyebrow">' . esc_html( (string) $s['format'] ) . '</p>';
		}
		if ( ! empty( $s['title'] ) ) {
			printf( '<%1$s class="czs-offering__title">%2$s</%1$s>', tag_escape( $tag ), esc_html( (string) $s['title'] ) );
		}
		echo $this->paragraphs( $s['blurb'] ?? '', 'czs-offering__blurb' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
		if ( ! empty( $s['price'] ) ) {
			echo '<p class="czs-offering__price">' . esc_html( (string) $s['price'] ) . '</p>';
		}
		$this->buttons( $s );
		echo '</div></article>';
	}
}
