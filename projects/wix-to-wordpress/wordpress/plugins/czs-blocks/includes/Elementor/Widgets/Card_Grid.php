<?php
/**
 * Card grid: values, features, lists of three to six items.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;
use Elementor\Repeater;

defined( 'ABSPATH' ) || exit;

class Card_Grid extends Base_Widget {

	public function get_name(): string {
		return 'czs-card-grid';
	}

	public function get_title(): string {
		return esc_html__( 'CS Card Grid', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-gallery-grid';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'cards', 'grid', 'values', 'features', 'list' ) );
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Card grid', 'czs-blocks' ) ) );

		$this->add_heading_controls( esc_html__( 'Section heading (optional)', 'czs-blocks' ), 'h2', array( 'h2', 'h3' ) );
		$this->add_control(
			'intro',
			array(
				'label' => esc_html__( 'Intro text (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXTAREA,
				'rows'  => 3,
			)
		);

		$card = new Repeater();
		$card->add_control(
			'title',
			array(
				'label'       => esc_html__( 'Card title', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXT,
				'label_block' => true,
			)
		);
		$card->add_control(
			'text',
			array(
				'label' => esc_html__( 'Card text', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXTAREA,
				'rows'  => 4,
			)
		);
		$card->add_control(
			'image',
			array(
				'label' => esc_html__( 'Image (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::MEDIA,
			)
		);
		$card->add_control(
			'link_text',
			array(
				'label' => esc_html__( 'Link text (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXT,
			)
		);
		$card->add_control(
			'link',
			array(
				'label'   => esc_html__( 'Link', 'czs-blocks' ),
				'type'    => Controls_Manager::URL,
				'options' => array( 'url', 'is_external', 'nofollow' ),
			)
		);

		$this->add_control(
			'cards',
			array(
				'label'       => esc_html__( 'Cards', 'czs-blocks' ),
				'type'        => Controls_Manager::REPEATER,
				'fields'      => $card->get_controls(),
				'title_field' => '{{{ title }}}',
				'default'     => array(
					array( 'title' => esc_html__( 'Card title', 'czs-blocks' ), 'text' => esc_html__( 'A sentence or two about this item.', 'czs-blocks' ) ),
					array( 'title' => esc_html__( 'Card title', 'czs-blocks' ), 'text' => esc_html__( 'A sentence or two about this item.', 'czs-blocks' ) ),
					array( 'title' => esc_html__( 'Card title', 'czs-blocks' ), 'text' => esc_html__( 'A sentence or two about this item.', 'czs-blocks' ) ),
				),
			)
		);
		$this->add_control(
			'columns',
			array(
				'label'   => esc_html__( 'Cards per row (large screens)', 'czs-blocks' ),
				'type'    => Controls_Manager::SELECT,
				'options' => array(
					'2' => '2',
					'3' => '3',
					'4' => '4',
				),
				'default' => '3',
			)
		);
		$this->add_control(
			'card_title_tag',
			array(
				'label'       => esc_html__( 'Card title level', 'czs-blocks' ),
				'type'        => Controls_Manager::SELECT,
				'options'     => array(
					'h2' => 'H2',
					'h3' => 'H3',
					'h4' => 'H4',
				),
				'default'     => 'h3',
				'description' => esc_html__( 'H3 under a section heading; H2 if the grid has no heading.', 'czs-blocks' ),
			)
		);
		$this->end_controls_section();

		$this->add_look_section( 'tint' );
	}

	protected function render(): void {
		$s     = $this->get_settings_for_display();
		$cards = is_array( $s['cards'] ?? null ) ? $s['cards'] : array();
		$cols  = in_array( (string) ( $s['columns'] ?? '' ), array( '2', '3', '4' ), true ) ? (string) $s['columns'] : '3';
		$ctag  = $this->tag( $s['card_title_tag'] ?? 'h3', array( 'h2', 'h3', 'h4' ), 'h3' );

		$this->open_section( 'card-grid', $s );
		echo '<div class="czs-inner">';
		if ( ! empty( $s['heading'] ) || ! empty( $s['intro'] ) ) {
			echo '<header class="czs-section-head">';
			$this->heading( $s, 'czs-title', 'h2' );
			echo $this->paragraphs( $s['intro'] ?? '', 'czs-lead' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
			echo '</header>';
		}
		if ( ! $cards ) {
			$this->editor_hint( __( 'Add cards in the Content tab.', 'czs-blocks' ) );
		}
		printf( '<ul class="czs-cards czs-cols-%s" role="list">', esc_attr( $cols ) );
		foreach ( $cards as $card ) {
			$has_image = $this->has_image( $card['image'] ?? null );
			echo '<li class="czs-card' . ( $has_image ? ' czs-card--image' : '' ) . '">';
			if ( $has_image ) {
				echo '<div class="czs-card__media">';
				echo $this->image( $card['image'], 'medium_large', 'czs-card__img', '(min-width: 1024px) ' . intval( 100 / (int) $cols ) . 'vw, (min-width: 640px) 50vw, 100vw' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in image().
				echo '</div>';
			}
			echo '<div class="czs-card__body">';
			if ( ! empty( $card['title'] ) ) {
				printf( '<%1$s class="czs-card__title">%2$s</%1$s>', tag_escape( $ctag ), esc_html( (string) $card['title'] ) );
			}
			echo $this->paragraphs( $card['text'] ?? '', 'czs-card__text' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
			if ( ! empty( $card['link_text'] ) && $this->has_link( $card['link'] ?? null ) ) {
				printf(
					'<a class="czs-card__link"%s>%s<span aria-hidden="true"> &rarr;</span></a>',
					$this->link_attrs( $card['link'] ), // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in link_attrs().
					esc_html( (string) $card['link_text'] )
				);
			}
			echo '</div></li>';
		}
		echo '</ul></div></section>';
	}
}
