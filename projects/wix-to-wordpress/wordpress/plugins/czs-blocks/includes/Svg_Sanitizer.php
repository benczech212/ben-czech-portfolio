<?php
/**
 * Allow-list SVG sanitizer (DOMDocument, no dependencies).
 * Removes scripts, event handlers, foreign content, external references,
 * DOCTYPE/entities and anything not on the element/attribute allow-list.
 *
 * @package CzechStudios\Blocks
 */

namespace CzechStudios\Blocks;

defined( 'ABSPATH' ) || exit;

final class Svg_Sanitizer {

	private const ELEMENTS = array(
		'svg', 'g', 'defs', 'symbol', 'use', 'title', 'desc', 'metadata', 'style',
		'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
		'text', 'tspan', 'textpath', 'lineargradient', 'radialgradient', 'stop',
		'clippath', 'mask', 'pattern', 'filter', 'marker', 'image', 'switch',
		'feblend', 'fecolormatrix', 'fecomponenttransfer', 'fecomposite', 'feflood',
		'fegaussianblur', 'femerge', 'femergenode', 'feoffset', 'fefunca', 'fefuncb',
		'fefuncg', 'fefuncr', 'fedropshadow', 'femorphology', 'feturbulence', 'fedisplacementmap',
	);

	private const ATTRIBUTES = array(
		'id', 'class', 'style', 'viewbox', 'width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2',
		'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'transform', 'fill', 'fill-opacity', 'fill-rule',
		'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit',
		'stroke-dasharray', 'stroke-dashoffset', 'stroke-opacity', 'opacity', 'clip-path', 'clip-rule',
		'mask', 'filter', 'offset', 'stop-color', 'stop-opacity', 'gradientunits', 'gradienttransform',
		'spreadmethod', 'fx', 'fy', 'fr', 'preserveaspectratio', 'version', 'xmlns', 'xmlns:xlink',
		'href', 'xlink:href', 'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor',
		'dominant-baseline', 'letter-spacing', 'dx', 'dy', 'rotate', 'textlength', 'lengthadjust',
		'patternunits', 'patterncontentunits', 'patterntransform', 'maskunits', 'maskcontentunits',
		'clippathunits', 'filterunits', 'primitiveunits', 'in', 'in2', 'result', 'stddeviation',
		'mode', 'operator', 'k1', 'k2', 'k3', 'k4', 'values', 'type', 'tablevalues', 'slope',
		'intercept', 'amplitude', 'exponent', 'flood-color', 'flood-opacity', 'basefrequency',
		'numoctaves', 'seed', 'stitchtiles', 'scale', 'xchannelselector', 'ychannelselector',
		'radius', 'markerwidth', 'markerheight', 'refx', 'refy', 'orient', 'markerunits',
		'visibility', 'display', 'overflow', 'color', 'enable-background', 'xml:space',
		'aria-hidden', 'aria-label', 'role', 'focusable', 'data-name',
	);

	/**
	 * @return string|null Clean SVG markup, or null if it cannot be parsed safely.
	 */
	public static function sanitize( string $svg ): ?string {
		$svg = trim( $svg );
		if ( '' === $svg || strlen( $svg ) > 2 * MB_IN_BYTES ) {
			return null;
		}
		// Reject DOCTYPE/ENTITY outright: blocks XXE and billion-laughs.
		if ( preg_match( '/<!(DOCTYPE|ENTITY)/i', $svg ) ) {
			return null;
		}

		$dom                     = new \DOMDocument();
		$dom->preserveWhiteSpace = false;
		$prev                    = libxml_use_internal_errors( true );
		$ok                      = $dom->loadXML( $svg, LIBXML_NONET | LIBXML_NOBLANKS );
		libxml_clear_errors();
		libxml_use_internal_errors( $prev );

		if ( ! $ok || ! $dom->documentElement || 'svg' !== strtolower( $dom->documentElement->localName ) ) {
			return null;
		}

		self::clean_node( $dom->documentElement );

		// Drop processing instructions and comments anywhere in the tree.
		$xpath = new \DOMXPath( $dom );
		foreach ( iterator_to_array( $xpath->query( '//comment() | //processing-instruction()' ) ) as $node ) {
			$node->parentNode?->removeChild( $node );
		}

		$out = $dom->saveXML( $dom->documentElement );
		return false === $out ? null : '<?xml version="1.0" encoding="UTF-8"?>' . "\n" . $out;
	}

	private static function clean_node( \DOMElement $el ): void {
		// Walk children first (copy the list, we mutate it).
		foreach ( iterator_to_array( $el->childNodes ) as $child ) {
			if ( $child instanceof \DOMElement ) {
				if ( ! in_array( strtolower( $child->localName ), self::ELEMENTS, true ) ) {
					$el->removeChild( $child );
					continue;
				}
				self::clean_node( $child );
			} elseif ( $child instanceof \DOMCdataSection && 'style' !== strtolower( $el->localName ) ) {
				$el->removeChild( $child );
			}
		}

		if ( 'style' === strtolower( $el->localName ) ) {
			$css = self::clean_css( $el->textContent );
			while ( $el->firstChild ) {
				$el->removeChild( $el->firstChild );
			}
			$el->appendChild( $el->ownerDocument->createTextNode( $css ) );
		}

		foreach ( iterator_to_array( $el->attributes ) as $attr ) {
			$name  = strtolower( $attr->nodeName );
			$value = (string) $attr->nodeValue;

			if ( str_starts_with( $name, 'on' ) || ! in_array( $name, self::ATTRIBUTES, true ) ) {
				$el->removeAttributeNode( $attr );
				continue;
			}
			if ( 'href' === $name || 'xlink:href' === $name ) {
				// Only in-document references (#id) and embedded raster images.
				$ok = str_starts_with( $value, '#' )
					|| ( 'image' === strtolower( $el->localName ) && preg_match( '#^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\s]+$#i', $value ) );
				if ( ! $ok ) {
					$el->removeAttributeNode( $attr );
				}
				continue;
			}
			if ( 'style' === $name ) {
				$el->setAttribute( 'style', self::clean_css( $value ) );
				continue;
			}
			if ( preg_match( '/(javascript|vbscript|data)\s*:|expression\s*\(|url\s*\(\s*[\'"]?\s*(?!#)/i', $value ) ) {
				$el->removeAttributeNode( $attr );
			}
		}
	}

	private static function clean_css( string $css ): string {
		$css = preg_replace( '/@import[^;]*;?/i', '', $css ) ?? '';
		$css = preg_replace( '/expression\s*\([^)]*\)/i', '', $css ) ?? '';
		$css = preg_replace( '/(javascript|vbscript)\s*:/i', '', $css ) ?? '';
		// url(...) only for in-document references.
		$css = preg_replace( '/url\s*\(\s*[\'"]?\s*(?!#)[^)]*\)/i', 'none', $css ) ?? '';
		return str_replace( array( '<', '>' ), '', $css );
	}
}
