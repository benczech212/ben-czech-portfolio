<?php
/**
 * People: founder/team cards pulled from the shared "People (bios)" records,
 * so each bio is written once and reused on every page.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use CzechStudios\Blocks\People as People_Records;
use Elementor\Controls_Manager;

defined( 'ABSPATH' ) || exit;

class People extends Base_Widget {

	private const SOCIALS = array(
		'_czs_person_instagram' => 'Instagram',
		'_czs_person_facebook'  => 'Facebook',
		'_czs_person_pinterest' => 'Pinterest',
		'_czs_person_website'   => 'Website',
	);

	public function get_name(): string {
		return 'czs-people';
	}

	public function get_title(): string {
		return esc_html__( 'CS People', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-person';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'people', 'team', 'founders', 'bio' ) );
	}

	protected function is_dynamic_content(): bool {
		return true; // Bios are edited elsewhere; never serve a stale cached render.
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'People', 'czs-blocks' ) ) );

		$this->add_heading_controls( esc_html__( 'Section heading', 'czs-blocks' ), 'h2', array( 'h2', 'h3' ) );
		$this->add_control(
			'intro',
			array(
				'label' => esc_html__( 'Intro text (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXTAREA,
				'rows'  => 3,
			)
		);
		$this->add_control(
			'people',
			array(
				'label'       => esc_html__( 'Who to show', 'czs-blocks' ),
				'type'        => Controls_Manager::SELECT2,
				'multiple'    => true,
				'label_block' => true,
				'options'     => People_Records::options(),
				'description' => esc_html__( 'Bios, photos and links are edited once under People (bios) in the dashboard.', 'czs-blocks' ),
			)
		);
		$this->add_control(
			'layout',
			array(
				'label'   => esc_html__( 'Layout', 'czs-blocks' ),
				'type'    => Controls_Manager::SELECT,
				'options' => array(
					'cards' => esc_html__( 'Side-by-side cards', 'czs-blocks' ),
					'rows'  => esc_html__( 'Full-width rows (photo alternates sides)', 'czs-blocks' ),
				),
				'default' => 'cards',
			)
		);
		$this->add_control(
			'show_bio',
			array(
				'label'        => esc_html__( 'Show bio text', 'czs-blocks' ),
				'type'         => Controls_Manager::SWITCHER,
				'return_value' => 'yes',
				'default'      => 'yes',
			)
		);
		$this->add_control(
			'show_links',
			array(
				'label'        => esc_html__( 'Show social and profile links', 'czs-blocks' ),
				'type'         => Controls_Manager::SWITCHER,
				'return_value' => 'yes',
				'default'      => 'yes',
			)
		);
		$this->end_controls_section();

		$this->add_look_section();
	}

	protected function render(): void {
		$s      = $this->get_settings_for_display();
		$slugs  = array_map( 'strval', (array) ( $s['people'] ?? array() ) );
		$people = People_Records::get_by_slugs( $slugs );
		$layout = 'rows' === ( $s['layout'] ?? '' ) ? 'rows' : 'cards';

		$this->open_section( 'people', $s, array( 'czs-people--' . $layout ) );
		echo '<div class="czs-inner">';
		if ( ! empty( $s['heading'] ) || ! empty( $s['intro'] ) ) {
			echo '<header class="czs-section-head">';
			$this->heading( $s, 'czs-title', 'h2' );
			echo $this->paragraphs( $s['intro'] ?? '', 'czs-lead' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
			echo '</header>';
		}
		if ( ! $people ) {
			$this->editor_hint( __( 'Pick people in the Content tab. Add or edit bios under People (bios) in the dashboard.', 'czs-blocks' ) );
		}
		$name_tag = $this->tag( ( $s['heading_tag'] ?? 'h2' ) === 'h3' ? 'h4' : 'h3', array( 'h3', 'h4' ), 'h3' );
		echo '<ul class="czs-people__list" role="list">';
		foreach ( $people as $person ) {
			$this->render_person( $person, $s, $name_tag );
		}
		echo '</ul></div></section>';
	}

	private function render_person( \WP_Post $person, array $s, string $name_tag ): void {
		$role  = (string) get_post_meta( $person->ID, '_czs_person_role', true );
		$photo = (int) get_post_thumbnail_id( $person );

		echo '<li class="czs-person">';
		if ( $photo ) {
			echo '<div class="czs-person__media">';
			echo wp_get_attachment_image( $photo, 'medium_large', false, array( 'class' => 'czs-person__img', 'sizes' => '(min-width: 900px) 360px, 80vw' ) );
			echo '</div>';
		}
		echo '<div class="czs-person__body">';
		printf( '<%1$s class="czs-person__name">%2$s</%1$s>', tag_escape( $name_tag ), esc_html( get_the_title( $person ) ) );
		if ( '' !== $role ) {
			echo '<p class="czs-person__role">' . nl2br( esc_html( $role ) ) . '</p>';
		}
		if ( 'yes' === ( $s['show_bio'] ?? 'yes' ) && '' !== trim( $person->post_content ) ) {
			echo '<div class="czs-person__bio czs-prose">' . wp_kses_post( wpautop( $person->post_content ) ) . '</div>';
		}
		if ( 'yes' === ( $s['show_links'] ?? 'yes' ) ) {
			$this->render_links( $person );
		}
		echo '</div></li>';
	}

	private function render_links( \WP_Post $person ): void {
		$socials = '';
		foreach ( self::SOCIALS as $key => $label ) {
			$url = (string) get_post_meta( $person->ID, $key, true );
			if ( '' === $url ) {
				continue;
			}
			$socials .= sprintf(
				'<li><a class="czs-social" href="%1$s" target="_blank" rel="noopener me"><span class="screen-reader-text">%2$s</span>%3$s</a></li>',
				esc_url( $url ),
				/* translators: 1: network name, 2: person name. */
				esc_html( sprintf( __( '%1$s: %2$s (opens in a new tab)', 'czs-blocks' ), $label, get_the_title( $person ) ) ),
				self::icon( $label ) // Static SVG markup below.
			);
		}
		$label = (string) get_post_meta( $person->ID, '_czs_person_profile_label', true );
		$url   = (string) get_post_meta( $person->ID, '_czs_person_profile_url', true );

		if ( '' === $socials && ( '' === $label || '' === $url ) ) {
			return;
		}
		echo '<div class="czs-person__links">';
		if ( '' !== $label && '' !== $url ) {
			printf( '<a class="czs-card__link" href="%s">%s<span aria-hidden="true"> &rarr;</span></a>', esc_url( $url ), esc_html( $label ) );
		}
		if ( '' !== $socials ) {
			echo '<ul class="czs-socials" role="list">' . $socials . '</ul>'; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- built from escaped parts.
		}
		echo '</div>';
	}

	/** Small inline icons (no icon font, no request). */
	private static function icon( string $network ): string {
		$paths = array(
			'Instagram' => 'M12 7.3A4.7 4.7 0 1 0 12 16.7 4.7 4.7 0 0 0 12 7.3Zm0 7.7a3 3 0 1 1 0-6 3 3 0 0 1 0 6Zm6-7.9a1.1 1.1 0 1 1-2.2 0 1.1 1.1 0 0 1 2.2 0ZM12 3.6c2.7 0 3 0 4.1.1 2.7.1 4 1.4 4.1 4.1.1 1.1.1 1.4.1 4.1s0 3-.1 4.1c-.1 2.7-1.4 4-4.1 4.1-1.1.1-1.4.1-4.1.1s-3 0-4.1-.1c-2.7-.1-4-1.4-4.1-4.1C3.7 15 3.6 14.7 3.6 12s0-3 .1-4.1c.1-2.7 1.4-4 4.1-4.1 1.1-.1 1.4-.2 4.2-.2ZM12 2c-2.7 0-3.1 0-4.1.1C4.2 2.2 2.2 4.2 2.1 7.9 2 8.9 2 9.3 2 12s0 3.1.1 4.1c.2 3.7 2.2 5.7 5.8 5.8 1 .1 1.4.1 4.1.1s3.1 0 4.1-.1c3.6-.2 5.7-2.2 5.8-5.8.1-1 .1-1.4.1-4.1s0-3.1-.1-4.1c-.2-3.6-2.2-5.7-5.8-5.8C15.1 2 14.7 2 12 2Z',
			'Facebook'  => 'M22 12a10 10 0 1 0-11.6 9.9v-7H7.9V12h2.5V9.8c0-2.5 1.5-3.9 3.8-3.9 1.1 0 2.2.2 2.2.2v2.5h-1.3c-1.2 0-1.6.8-1.6 1.6V12h2.8l-.4 2.9h-2.3v7A10 10 0 0 0 22 12Z',
			'Pinterest' => 'M12 2a10 10 0 0 0-3.6 19.3c-.1-.8-.2-2 0-2.9l1.2-5s-.3-.6-.3-1.5c0-1.4.8-2.4 1.8-2.4.8 0 1.3.6 1.3 1.4 0 .9-.6 2.2-.9 3.4-.2 1 .5 1.9 1.6 1.9 1.9 0 3.3-2 3.3-4.9 0-2.6-1.8-4.4-4.5-4.4a4.6 4.6 0 0 0-4.8 4.7c0 .9.4 1.9.8 2.5.1.1.1.2.1.3l-.3 1.2c0 .2-.2.3-.4.2-1.4-.7-2.3-2.7-2.3-4.4 0-3.6 2.6-6.9 7.5-6.9 3.9 0 7 2.8 7 6.6 0 3.9-2.5 7.1-5.9 7.1-1.2 0-2.2-.6-2.6-1.3l-.7 2.7c-.3 1-1 2.2-1.4 2.9A10 10 0 1 0 12 2Z',
			'Website'   => 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm6.9 6h-3a15.6 15.6 0 0 0-1.4-3.6A8 8 0 0 1 18.9 8ZM12 4c.8 1.2 1.5 2.5 1.9 4h-3.8c.4-1.4 1.1-2.8 1.9-4ZM4.3 14a8.2 8.2 0 0 1 0-4h3.4a16.5 16.5 0 0 0 0 4H4.3Zm.8 2h3a15.6 15.6 0 0 0 1.4 3.6A8 8 0 0 1 5.1 16Zm3-8h-3a8 8 0 0 1 4.3-3.6C8.9 5.5 8.4 6.7 8.1 8ZM12 20c-.8-1.2-1.5-2.5-1.9-4h3.8c-.4 1.4-1.1 2.8-1.9 4Zm2.3-6H9.7a14.7 14.7 0 0 1 0-4h4.6a14.7 14.7 0 0 1 0 4Zm.3 5.6c.6-1.1 1.1-2.3 1.4-3.6h3a8 8 0 0 1-4.4 3.6Zm1.7-5.6a16.5 16.5 0 0 0 0-4h3.4a8.2 8.2 0 0 1 0 4h-3.4Z',
		);
		$d = $paths[ $network ] ?? $paths['Website'];
		return '<svg class="czs-icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path fill="currentColor" d="' . esc_attr( $d ) . '"/></svg>';
	}
}
