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

import 'models.dart';

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
    this.label = '',
  });
  final List<LatLngPoint> points;
  final double distanceM;
  final double durationS;
  final String source; // 'OSRM' or 'Google Directions'

  /// Display label for route comparison ('Fastest', 'Alternative 1',
  /// …). Empty for a single route fetched via getRoute.
  final String label;
}

/// Risk score of one route: the summed typical-weekday risk score of
/// the cameras within [nearM] of it (the same 300 m rule the Route
/// screen uses for its near-route list). Lower is safer. Uses the
/// camera's stored typical score, so scoring costs no backend calls.
({double score, int cameraCount}) scoreRoute(
  RouteResult route,
  List<Intersection> cameras, {
  double nearM = 300,
}) {
  final step = (route.points.length / 100).ceil().clamp(1, 1000);
  final seen = <String>{};
  var score = 0.0;
  for (var i = 0; i < route.points.length; i += step) {
    final p = route.points[i];
    for (final cam in cameras) {
      if (cam.latitude == null || cam.longitude == null) continue;
      if (seen.contains(cam.cameraId)) continue;
      final d = RouteService.distanceM(
        p,
        LatLngPoint(cam.latitude!, cam.longitude!),
      );
      if (d <= nearM) {
        seen.add(cam.cameraId);
        score += cam.typicalWeekdayScore;
      }
    }
  }
  return (score: score, cameraCount: seen.length);
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
    final routes = await getRoutes(from, to);
    return routes.first;
  }

  /// Route comparison (Exclusive): the fastest route plus up to
  /// [alternatives] alternatives, each labelled for the comparison
  /// UI. OSRM takes `alternatives=N` (main + N); Google takes
  /// `alternatives=true`. When the provider returns only one route,
  /// the list has one entry — the comparison UI then hides itself.
  /// Same fallback as getRoute: OSRM first, Google when OSRM fails
  /// and a key is configured.
  Future<List<RouteResult>> getRoutes(
    LatLngPoint from,
    LatLngPoint to, {
    int alternatives = 2,
  }) async {
    try {
      return await _osrmRoutes(from, to, alternatives: alternatives);
    } on Exception catch (e) {
      if (googleMapsApiKey.isEmpty) {
        throw RoutingException(
          'OSRM routing failed ($e). Google Directions fallback needs '
          'a Google Maps API key in lib/config.dart with the Directions '
          'API enabled — see README.',
        );
      }
      try {
        return await _googleRoutes(from, to);
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

  Future<List<RouteResult>> _osrmRoutes(
    LatLngPoint from,
    LatLngPoint to, {
    int alternatives = 2,
  }) async {
    final uri = Uri.parse(
      '$_osrmBase/route/v1/driving/'
      '${from.longitude},${from.latitude};${to.longitude},${to.latitude}'
      '?overview=full&geometries=geojson&alternatives=$alternatives',
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
    final out = <RouteResult>[];
    for (var i = 0; i < routes.length; i++) {
      final Map<String, dynamic> route = routes[i] as Map<String, dynamic>;
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
      if (points.isEmpty) continue;
      out.add(
        RouteResult(
          points: points,
          distanceM: (route['distance'] as num? ?? 0).toDouble(),
          durationS: (route['duration'] as num? ?? 0).toDouble(),
          source: 'OSRM',
          label: i == 0 ? 'Fastest' : 'Alternative $i',
        ),
      );
    }
    if (out.isEmpty) throw RoutingException('OSRM route has no geometry.');
    return out;
  }

  Future<List<RouteResult>> _googleRoutes(
    LatLngPoint from,
    LatLngPoint to,
  ) async {
    final uri = Uri.https('maps.googleapis.com', '/maps/api/directions/json', {
      'origin': '${from.latitude},${from.longitude}',
      'destination': '${to.latitude},${to.longitude}',
      'alternatives': 'true',
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
    final out = <RouteResult>[];
    for (var i = 0; i < routes.length; i++) {
      final Map<String, dynamic> route = routes[i] as Map<String, dynamic>;
      final List<dynamic> legs = (route['legs'] as List?) ?? [];
      double distanceM = 0;
      double durationS = 0;
      for (final leg in legs) {
        final Map<String, dynamic> l = leg as Map<String, dynamic>;
        distanceM += ((l['distance'] as Map?)?['value'] as num? ?? 0)
            .toDouble();
        durationS += ((l['duration'] as Map?)?['value'] as num? ?? 0)
            .toDouble();
      }
      final String polyline =
          ((route['overview_polyline'] as Map?)?['points'] ?? '').toString();
      final points = _decodePolyline(polyline);
      if (points.isEmpty) continue;
      out.add(
        RouteResult(
          points: points,
          distanceM: distanceM,
          durationS: durationS,
          source: 'Google Directions',
          label: i == 0 ? 'Fastest' : 'Alternative $i',
        ),
      );
    }
    if (out.isEmpty) {
      throw RoutingException('Google Directions returned no geometry.');
    }
    return out;
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
