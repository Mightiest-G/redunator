// Update check + theme setting tests (dark mode + in-app updates).
import 'dart:convert';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/services/settings_service.dart';
import 'package:redunator/services/update_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

Map<String, dynamic> latestJson({
  String? tag = 'apk-5',
  int? build = 5,
  String? downloadUrl =
      'https://github.com/Mightiest-G/redunator/releases/download/apk-5/app-release.apk',
}) =>
    {
      'tag': tag,
      'build': build,
      'name': 'Redunator APK (build 5)',
      'published_at': '2026-11-18T00:00:00Z',
      'download_url': downloadUrl,
      'notes': 'Dark mode + update check.',
      'reason': null,
    };

RedunatorApiClient mockClient(Map<String, dynamic> Function() body) =>
    RedunatorApiClient(
      baseUrl: 'https://example.invalid',
      httpClient: MockClient((req) async {
        expect(req.url.path, '/app/latest');
        return http.Response(jsonEncode(body()), 200);
      }),
    );

void main() {
  group('UpdateService.isUpdateAvailable', () {
    test('newer published build is an update', () {
      expect(
        UpdateService.isUpdateAvailable(currentBuild: 3, latestBuild: 5),
        isTrue,
      );
    });

    test('same build is not an update', () {
      expect(
        UpdateService.isUpdateAvailable(currentBuild: 5, latestBuild: 5),
        isFalse,
      );
    });

    test('older published build is not an update', () {
      expect(
        UpdateService.isUpdateAvailable(currentBuild: 6, latestBuild: 5),
        isFalse,
      );
    });

    test('no published build info is not an update', () {
      expect(
        UpdateService.isUpdateAvailable(currentBuild: 3, latestBuild: null),
        isFalse,
      );
    });
  });

  group('UpdateService.checkForUpdate', () {
    test('returns info when a newer release exists', () async {
      final info = await UpdateService.checkForUpdate(
        mockClient(() => latestJson()),
        currentBuildOverride: 3,
      );
      expect(info, isNotNull);
      expect(info!.build, 5);
      expect(info.tag, 'apk-5');
      expect(info.downloadUrl, contains('apk-5/app-release.apk'));
    });

    test('returns null when up to date', () async {
      final info = await UpdateService.checkForUpdate(
        mockClient(() => latestJson()),
        currentBuildOverride: 5,
      );
      expect(info, isNull);
    });

    test('returns null when the backend has no release info', () async {
      final info = await UpdateService.checkForUpdate(
        mockClient(() => {
              'tag': null,
              'build': null,
              'download_url': null,
              'reason': 'github_token_not_configured',
            }),
        currentBuildOverride: 3,
      );
      expect(info, isNull);
    });

    test('returns null when the backend call fails', () async {
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient(
          (_) async => http.Response('boom', 500),
        ),
      );
      final info = await UpdateService.checkForUpdate(
        client,
        currentBuildOverride: 3,
      );
      expect(info, isNull);
    });
  });

  group('AppSettings theme mode', () {
    setUp(() {
      SharedPreferences.setMockInitialValues({});
    });

    test('defaults to following the phone', () async {
      final s = await AppSettings.load();
      expect(s.themeMode, 'system');
      expect(s.themeModeEnum, ThemeMode.system);
    });

    test('dark persists and maps to ThemeMode.dark', () async {
      final s = await AppSettings.load();
      await s.setThemeMode('dark');
      expect(s.themeMode, 'dark');
      expect(s.themeModeEnum, ThemeMode.dark);
      final reloaded = await AppSettings.load();
      expect(reloaded.themeMode, 'dark');
    });

    test('light maps to ThemeMode.light; junk falls back to system',
        () async {
      final s = await AppSettings.load();
      await s.setThemeMode('light');
      expect(s.themeModeEnum, ThemeMode.light);
      await s.setThemeMode('neon');
      expect(s.themeMode, 'system');
      expect(s.themeModeEnum, ThemeMode.system);
    });
  });
}
