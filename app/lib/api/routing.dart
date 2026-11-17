// Routing for the Route screen.
//
// PRIMARY SOURCE: OSRM (Open Source Routing Machine) public demo
// server — https://router.project-osrm.org — free, no API key, no
// account. Chosen because it needs nothing from Gabriel and works
// out of the box; the route geometry comes back as GeoJSON.
// Rate limits on the public demo server are modest (it is a demo);
// if it ever becomes unreliable, the Google fallback below takes over.
//
// FALLBACK: Google Maps Directions API, using the SAME Google Maps
// API key as the map (lib/config.dart). It is only used when OSRM
// fails AND a key is configured. To enable it Gabriel only needs the
// "Directions API" enabled on the same key in Google Cloud console —
// no second key, no extra account.
// If both fail, the screen shows the exact error and the OSRM URL so
// Gabriel can check it in a browser.
import 'dart:convert';
import 'dart:math' as math;

import 'package:http/http.dart' as http;

class LatLngPoint {
  const LatLngPoint(this.latitude, this.longitude);
  final double latitude;
  final double longitude;

  Map<String, dynamic> toJson() => {'lat': latitude, 'lng': longitude};
}

class RouteResult {
  RouteResult({
    required this.points,
    required this.distanceM,
    required this.durationS,
    required this.source,
  });
  final List<LatLngPoint> points;
  final double distanceM;
  final double durationS;
  final String source; // 'OSRM' or 'Google Directions'
}

class RoutingException implements Exception {
  RoutingException(this.message);
  final String message;
  @override
  String toString() => 'RoutingException: $message';
}

class RouteService {
  RouteService({http.Client? httpClient, this.googleMapsApiKey = ''})
      : _http = httpClient ?? http.Client();

  final http.Client _http;
  final String googleMapsApiKey;

  static const String _osrmBase = 'https://router.project-osrm.org';

  Future<RouteResult> getRoute(LatLngPoint from, LatLngPoint to) async {
    try {
      return await _osrmRoute(from, to);
    } on Exception catch (e) {
      if (googleMapsApiKey.isEmpty) {
        throw RoutingException(
          'OSRM routing failed ($e). Google Directions fallback needs '
          'a Google Maps API key in lib/config.dart with the Directions '
          'API enabled — see README.',
        );
      }
      try {
        return await _googleRoute(from, to);
      } on Exception catch (g) {
        throw RoutingException(
          'OSRM failed ($e) and Google Directions also failed ($g). '
          'Check the route endpoints in a browser: '
          '$_osrmBase/route/v1/driving/'
          '${from.longitude},${from.latitude};${to.longitude},${to.latitude}',
        );
      }
    }
  }

  Future<RouteResult> _osrmRoute(LatLngPoint from, LatLngPoint to) async {
    final uri = Uri.parse(
      '$_osrmBase/route/v1/driving/'
      '${from.longitude},${from.latitude};${to.longitude},${to.latitude}'
      '?overview=full&geometries=geojson',
    );
    final resp = await _http.get(uri).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw RoutingException('OSRM HTTP ${resp.statusCode}');
    }
    final Map<String, dynamic> json =
        jsonDecode(resp.body) as Map<String, dynamic>;
    if (json['code'] != 'Ok') {
      throw RoutingException('OSRM: ${json['message'] ?? json['code']}');
    }
    final List<dynamic> routes = (json['routes'] as List?) ?? [];
    if (routes.isEmpty) throw RoutingException('OSRM returned no routes.');
    final Map<String, dynamic> route = routes.first as Map<String, dynamic>;
    final Map<String, dynamic> geometry =
        (route['geometry'] as Map).cast<String, dynamic>();
    final List<dynamic> coords = (geometry['coordinates'] as List?) ?? [];
    final points = coords.map((c) {
      final List<dynamic> pair = c as List<dynamic>;
      return LatLngPoint(
        (pair[1] as num).toDouble(),
        (pair[0] as num).toDouble(),
      );
    }).toList();
    if (points.isEmpty) throw RoutingException('OSRM route has no geometry.');
    return RouteResult(
      points: points,
      distanceM: (route['distance'] as num? ?? 0).toDouble(),
      durationS: (route['duration'] as num? ?? 0).toDouble(),
      source: 'OSRM',
    );
  }

  Future<RouteResult> _googleRoute(LatLngPoint from, LatLngPoint to) async {
    final uri = Uri.https('maps.googleapis.com', '/maps/api/directions/json', {
      'origin': '${from.latitude},${from.longitude}',
      'destination': '${to.latitude},${to.longitude}',
      'key': googleMapsApiKey,
    });
    final resp = await _http.get(uri).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw RoutingException('Google Directions HTTP ${resp.statusCode}');
    }
    final Map<String, dynamic> json =
        jsonDecode(resp.body) as Map<String, dynamic>;
    if (json['status'] != 'OK') {
      throw RoutingException(
        'Google Directions: ${json['status']} — ${json['error_message'] ?? ''}',
      );
    }
    final List<dynamic> routes = (json['routes'] as List?) ?? [];
    if (routes.isEmpty) {
      throw RoutingException('Google Directions returned no routes.');
    }
    final Map<String, dynamic> route = routes.first as Map<String, dynamic>;
    final List<dynamic> legs = (route['legs'] as List?) ?? [];
    double distanceM = 0;
    double durationS = 0;
    for (final leg in legs) {
      final Map<String, dynamic> l = leg as Map<String, dynamic>;
      distanceM += ((l['distance'] as Map?)?['value'] as num? ?? 0).toDouble();
      durationS += ((l['duration'] as Map?)?['value'] as num? ?? 0).toDouble();
    }
    final String polyline =
        ((route['overview_polyline'] as Map?)?['points'] ?? '').toString();
    final points = _decodePolyline(polyline);
    if (points.isEmpty) {
      throw RoutingException('Google Directions route has no geometry.');
    }
    return RouteResult(
      points: points,
      distanceM: distanceM,
      durationS: durationS,
      source: 'Google Directions',
    );
  }

  /// Google encoded-polyline decoder (standard algorithm).
  static List<LatLngPoint> _decodePolyline(String encoded) {
    final List<LatLngPoint> points = [];
    var index = 0;
    var lat = 0;
    var lng = 0;
    while (index < encoded.length) {
      var shift = 0;
      var result = 0;
      int b;
      do {
        b = encoded.codeUnitAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      final dlat = (result & 1) != 0 ? ~(result >> 1) : (result >> 1);
      lat += dlat;
      shift = 0;
      result = 0;
      do {
        b = encoded.codeUnitAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      final dlng = (result & 1) != 0 ? ~(result >> 1) : (result >> 1);
      lng += dlng;
      points.add(LatLngPoint(lat / 1e5, lng / 1e5));
    }
    return points;
  }

  /// Haversine distance in metres — used to find cameras near a route.
  static double distanceM(LatLngPoint a, LatLngPoint b) {
    const r = 6371000.0;
    final dLat = (b.latitude - a.latitude) * math.pi / 180;
    final dLon = (b.longitude - a.longitude) * math.pi / 180;
    final la1 = a.latitude * math.pi / 180;
    final la2 = b.latitude * math.pi / 180;
    final h = math.sin(dLat / 2) * math.sin(dLat / 2) +
        math.cos(la1) * math.cos(la2) * math.sin(dLon / 2) * math.sin(dLon / 2);
    return 2 * r * math.asin(math.sqrt(h));
  }
}
