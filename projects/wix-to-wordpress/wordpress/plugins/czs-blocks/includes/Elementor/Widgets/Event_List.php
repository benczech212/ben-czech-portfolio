<?php
/**
 * Event list: upcoming dates. For now the dates are typed in the widget; when
 * the events data exists (wave 2) a "source" option will feed it instead.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks\Elementor\Widgets;

use CzechStudios\Blocks\Elementor\Base_Widget;
use Elementor\Controls_Manager;
use Elementor\Repeater;

defined( 'ABSPATH' ) || exit;

class Event_List extends Base_Widget {

	public function get_name(): string {
		return 'czs-event-list';
	}

	public function get_title(): string {
		return esc_html__( 'CS Event List', 'czs-blocks' );
	}

	public function get_icon(): string {
		return 'eicon-calendar';
	}

	public function get_keywords(): array {
		return array_merge( parent::get_keywords(), array( 'events', 'dates', 'calendar', 'workshops' ) );
	}

	protected function is_dynamic_content(): bool {
		return true; // Past dates drop off by themselves.
	}

	protected function register_controls(): void {
		$this->start_controls_section( 'section_content', array( 'label' => esc_html__( 'Events', 'czs-blocks' ) ) );
		$this->add_heading_controls( esc_html__( 'Section heading (optional)', 'czs-blocks' ), 'h2', array( 'h2', 'h3' ) );

		$event = new Repeater();
		$event->add_control(
			'date',
			array(
				'label'          => esc_html__( 'Date and time', 'czs-blocks' ),
				'type'           => Controls_Manager::DATE_TIME,
				'picker_options' => array( 'enableTime' => true ),
			)
		);
		$event->add_control(
			'title',
			array(
				'label'       => esc_html__( 'Event name', 'czs-blocks' ),
				'type'        => Controls_Manager::TEXT,
				'label_block' => true,
			)
		);
		$event->add_control(
			'location',
			array(
				'label' => esc_html__( 'Where (e.g. Online, or a town)', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXT,
			)
		);
		$event->add_control(
			'text',
			array(
				'label' => esc_html__( 'Short description (optional)', 'czs-blocks' ),
				'type'  => Controls_Manager::TEXTAREA,
				'rows'  => 3,
			)
		);
		$event->add_control(
			'link_text',
			array(
				'label'   => esc_html__( 'Link text', 'czs-blocks' ),
				'type'    => Controls_Manager::TEXT,
				'default' => esc_html__( 'Details', 'czs-blocks' ),
			)
		);
		$event->add_control(
			'link',
			array(
				'label'   => esc_html__( 'Link', 'czs-blocks' ),
				'type'    => Controls_Manager::URL,
				'options' => array( 'url', 'is_external', 'nofollow' ),
			)
		);
		$this->add_control(
			'events',
			array(
				'label'       => esc_html__( 'Dates', 'czs-blocks' ),
				'type'        => Controls_Manager::REPEATER,
				'fields'      => $event->get_controls(),
				'title_field' => '{{{ date }}} {{{ title }}}',
			)
		);
		$this->add_control(
			'hide_past',
			array(
				'label'        => esc_html__( 'Hide dates that have passed', 'czs-blocks' ),
				'type'         => Controls_Manager::SWITCHER,
				'return_value' => 'yes',
				'default'      => 'yes',
			)
		);
		$this->add_control(
			'empty_text',
			array(
				'label'   => esc_html__( 'Text when nothing is scheduled', 'czs-blocks' ),
				'type'    => Controls_Manager::TEXT,
				'default' => esc_html__( 'New dates are coming soon.', 'czs-blocks' ),
			)
		);
		$this->end_controls_section();
		$this->add_look_section();
	}

	protected function render(): void {
		$s      = $this->get_settings_for_display();
		$tz     = wp_timezone();
		$now    = new \DateTimeImmutable( 'now', $tz );
		$events = array();
		foreach ( (array) ( $s['events'] ?? array() ) as $e ) {
			try {
				$when = empty( $e['date'] ) ? null : new \DateTimeImmutable( (string) $e['date'], $tz );
			} catch ( \Exception $ex ) {
				$when = null;
			}
			if ( $when && 'yes' === ( $s['hide_past'] ?? '' ) && $when < $now->setTime( 0, 0 ) ) {
				continue;
			}
			$events[] = array( $when, $e );
		}
		usort( $events, static fn( $a, $b ) => ( $a[0]?->getTimestamp() ?? PHP_INT_MAX ) <=> ( $b[0]?->getTimestamp() ?? PHP_INT_MAX ) );

		$this->open_section( 'event-list', $s );
		echo '<div class="czs-inner czs-inner--narrow">';
		if ( ! empty( $s['heading'] ) ) {
			echo '<header class="czs-section-head">';
			$this->heading( $s, 'czs-title', 'h2' );
			echo '</header>';
		}
		if ( ! $events ) {
			echo '<p class="czs-lead">' . esc_html( (string) ( $s['empty_text'] ?? '' ) ) . '</p>';
		} else {
			$etag = ( $s['heading_tag'] ?? 'h2' ) === 'h3' ? 'h4' : 'h3';
			echo '<ol class="czs-events" role="list">';
			foreach ( $events as [ $when, $e ] ) {
				echo '<li class="czs-event">';
				if ( $when ) {
					printf(
						'<time class="czs-event__date" datetime="%s"><span class="czs-event__month">%s</span><span class="czs-event__day">%s</span></time>',
						esc_attr( $when->format( 'c' ) ),
						esc_html( wp_date( 'M', $when->getTimestamp() ) ),
						esc_html( wp_date( 'j', $when->getTimestamp() ) )
					);
				}
				echo '<div class="czs-event__body">';
				printf( '<%1$s class="czs-event__title">%2$s</%1$s>', tag_escape( $etag ), esc_html( (string) ( $e['title'] ?? '' ) ) );
				$meta = array_filter(
					array(
						$when ? wp_date( get_option( 'date_format' ) . ' · ' . get_option( 'time_format' ), $when->getTimestamp() ) : '',
						(string) ( $e['location'] ?? '' ),
					)
				);
				if ( $meta ) {
					echo '<p class="czs-event__meta">' . esc_html( implode( ' · ', $meta ) ) . '</p>';
				}
				echo $this->paragraphs( $e['text'] ?? '', 'czs-event__text' ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in paragraphs().
				if ( ! empty( $e['link_text'] ) && $this->has_link( $e['link'] ?? null ) ) {
					printf(
						'<a class="czs-card__link"%s>%s<span class="screen-reader-text">: %s</span><span aria-hidden="true"> &rarr;</span></a>',
						$this->link_attrs( $e['link'] ), // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped in link_attrs().
						esc_html( (string) $e['link_text'] ),
						esc_html( (string) ( $e['title'] ?? '' ) )
					);
				}
				echo '</div></li>';
			}
			echo '</ol>';
		}
		echo '</div></section>';
	}
}
