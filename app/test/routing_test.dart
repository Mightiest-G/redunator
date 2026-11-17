// Routing tests: OSRM parsing, Google polyline decoding, haversine.
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/routing.dart';

void main() {
  group('RouteService', () {
    test('OSRM response parses to points + distance + duration', () async {
      final service = RouteService(
        httpClient: MockClient((req) async {
          expect(req.url.host, 'router.project-osrm.org');
          return http.Response(
            jsonEncode({
              'code': 'Ok',
              'routes': [
                {
                  'distance': 1200.5,
                  'duration': 300.25,
                  'geometry': {
                    'type': 'LineString',
                    'coordinates': [
                      [-87.6298, 41.8781],
                      [-87.6396, 41.9402],
                    ],
                  },
                },
              ],
            }),
            200,
          );
        }),
      );
      final route = await service.getRoute(
        const LatLngPoint(41.8781, -87.6298),
        const LatLngPoint(41.9402, -87.6396),
      );
      expect(route.source, 'OSRM');
      expect(route.distanceM, 1200.5);
      expect(route.durationS, 300.25);
      expect(route.points.length, 2);
      expect(route.points.first.latitude, closeTo(41.8781, 1e-6));
      expect(route.points.last.longitude, closeTo(-87.6396, 1e-6));
    });

    test('OSRM failure falls back to Google when a key is set', () async {
      var calls = 0;
      final service = RouteService(
        googleMapsApiKey: 'test-key',
        httpClient: MockClient((req) async {
          calls++;
          if (req.url.host == 'router.project-osrm.org') {
            return http.Response('{"code":"NoRoute"}', 200);
          }
          expect(req.url.host, 'maps.googleapis.com');
          // Encoded polyline for (38.5,-120.2) (39.1,-120.95) (40.7,-120.95)
          // — the canonical example from Google's polyline docs.
          return http.Response(
            jsonEncode({
              'status': 'OK',
              'routes': [
                {
                  'legs': [
                    {
                      'distance': {'value': 1000},
                      'duration': {'value': 120},
                    },
                  ],
                  'overview_polyline': {
                    'points': '_p~iF~ps|U_ulLnnqC_mqNvxq`@',
                  },
                },
              ],
            }),
            200,
          );
        }),
      );
      final route = await service.getRoute(
        const LatLngPoint(38.5, -120.2),
        const LatLngPoint(40.7, -120.95),
      );
      expect(route.source, 'Google Directions');
      expect(route.points.length, 3);
      expect(route.points.first.latitude, closeTo(38.5, 1e-4));
      expect(calls, 2);
    });

    test('OSRM failure without a key throws a helpful RoutingException',
        () async {
      final service = RouteService(
        httpClient: MockClient((req) async {
          return http.Response('Service Unavailable', 503);
        }),
      );
      expect(
        () => service.getRoute(
          const LatLngPoint(41.8781, -87.6298),
          const LatLngPoint(41.9402, -87.6396),
        ),
        throwsA(
          isA<RoutingException>().having(
            (e) => e.message,
            'message',
            contains('Google Maps API key'),
          ),
        ),
      );
    });

    test('haversine distance: downtown to Belmont ~7 km', () {
      final d = RouteService.distanceM(
        const LatLngPoint(41.8781, -87.6298),
        const LatLngPoint(41.9402, -87.6396),
      );
      expect(d, greaterThan(6000));
      expect(d, lessThan(8000));
    });
  });
}
