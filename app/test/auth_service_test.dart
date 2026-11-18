// Device-auth tests: the device UUID is stable, tokens are cached
// until near expiry, a 401 refreshes the token and retries once, and
// minting failures never throw out of the service.
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/auth_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

const String kTestDeviceId = '22222222-2222-4222-8222-222222222222';

RedunatorApiClient mintClient({int expiresIn = 2592000}) =>
    RedunatorApiClient(
      baseUrl: 'https://example.invalid',
      httpClient: MockClient((req) async {
        if (req.url.path == '/auth/device') {
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body['device_id'], kTestDeviceId);
          return http.Response(
            jsonEncode({
              'access_token': 'test-jwt-token',
              'token_type': 'bearer',
              'expires_in': expiresIn,
            }),
            200,
          );
        }
        return http.Response('{"status":"ok"}', 200);
      }),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('TokenService', () {
    test('load generates a UUID once and keeps it', () async {
      final auth = TokenService();
      final first = await auth.load();
      expect(
        first,
        matches(RegExp(
          r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
        )),
      );
      final second = await auth.load();
      expect(second, first);
      // A new instance reads the same stored id.
      expect(await TokenService().load(), first);
    });

    test('ensureToken mints once, then reuses the cached token', () async {
      var mintCalls = 0;
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient((req) async {
          if (req.url.path == '/auth/device') {
            mintCalls++;
            return http.Response(
              jsonEncode({
                'access_token': 'token-$mintCalls',
                'token_type': 'bearer',
                'expires_in': 2592000,
              }),
              200,
            );
          }
          return http.Response('{}', 200);
        }),
      );
      final auth = TokenService.withDeviceId(kTestDeviceId);
      expect(await auth.ensureToken(client), 'token-1');
      expect(client.authToken, 'token-1');
      expect(await auth.ensureToken(client), 'token-1');
      expect(mintCalls, 1);
    });

    test('ensureToken re-mints when the cached token is near expiry',
        () async {
      SharedPreferences.setMockInitialValues({
        'device_id': kTestDeviceId,
        'auth_token': 'stale-token',
        'auth_token_expiry_ms':
            DateTime.now().millisecondsSinceEpoch + 60 * 1000, // 1 min left
      });
      final auth = TokenService();
      await auth.load();
      final client = mintClient();
      expect(await auth.ensureToken(client), 'test-jwt-token');
      expect(client.authToken, 'test-jwt-token');
    });

    test('refreshToken returns null when minting fails (never throws)',
        () async {
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient((req) async {
          // Backend without a JWT secret answers 503.
          return http.Response(
            jsonEncode({'error': 'service_unavailable'}),
            503,
          );
        }),
      );
      final auth = TokenService.withDeviceId(kTestDeviceId);
      expect(await auth.refreshToken(client), isNull);
      expect(client.authToken, isNull);
    });
  });

  group('RedunatorApiClient 401 retry', () {
    test('401 refreshes the token once and retries', () async {
      var attempts = 0;
      var refreshCalls = 0;
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient((req) async {
          if (req.url.path == '/health') {
            attempts++;
            if (attempts == 1) {
              expect(req.headers['Authorization'], isNull);
              return http.Response('{"error":"unauthorized"}', 401);
            }
            expect(req.headers['Authorization'], 'Bearer fresh-token');
            return http.Response(
              jsonEncode({
                'status': 'ok',
                'model_version': 1,
                'latest_violation_date': '2026-10-29',
                'model_metrics': {'mae': 2.14, 'rmse': 3.3},
              }),
              200,
            );
          }
          return http.Response('{}', 404);
        }),
      );
      client.onUnauthorized = () async {
        refreshCalls++;
        client.authToken = 'fresh-token';
      };
      final health = await client.getHealth();
      expect(health.status, 'ok');
      expect(attempts, 2);
      expect(refreshCalls, 1);
    });

    test('401 with no handler surfaces the ApiException', () async {
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient((req) async {
          return http.Response('{"error":"unauthorized"}', 401);
        }),
      );
      await expectLater(
        () => client.getHealth(),
        throwsA(isA<ApiException>().having(
          (e) => e.statusCode,
          'statusCode',
          401,
        )),
      );
    });

    test('401 twice (refresh did not help) throws after one retry',
        () async {
      var attempts = 0;
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient((req) async {
          attempts++;
          return http.Response('{"error":"unauthorized"}', 401);
        }),
      );
      client.onUnauthorized = () async {};
      // expectLater (awaited): the matcher runs async, so the
      // attempts counter is only meaningful after it completes.
      await expectLater(
        () => client.getHealth(),
        throwsA(isA<ApiException>()),
      );
      expect(attempts, 2);
    });

    test('issueDeviceToken posts the device id and parses the mint',
        () async {
      final client = mintClient();
      final json = await client.issueDeviceToken(kTestDeviceId);
      expect(json['access_token'], 'test-jwt-token');
      expect(json['token_type'], 'bearer');
      expect(json['expires_in'], 2592000);
    });
  });
}
