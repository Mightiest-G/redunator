// In-app update check: compares this build (PackageInfo buildNumber,
// stamped by the GitHub Actions workflow as the run number) with the
// latest published release from GET /app/latest, and shows the update
// dialog. The download opens the release APK URL in the browser.
import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/client.dart';

class AppUpdateInfo {
  const AppUpdateInfo({
    required this.build,
    required this.tag,
    required this.downloadUrl,
    this.name,
  });
  final int build;
  final String tag;
  final String downloadUrl;
  final String? name;
}

class UpdateService {
  /// Pure comparison, unit-tested: an update is available only when
  /// the published build number is strictly greater than this build.
  static bool isUpdateAvailable({
    required int currentBuild,
    required int? latestBuild,
  }) =>
      latestBuild != null && latestBuild > currentBuild;

  /// Returns the update info when a newer release exists, null when
  /// up to date or when the backend has no release info (no token,
  /// GitHub unreachable, or any error — the check fails silent, the
  /// app keeps working). [currentBuildOverride] is a test seam.
  static Future<AppUpdateInfo?> checkForUpdate(
    RedunatorApiClient client, {
    int? currentBuildOverride,
  }) async {
    try {
      final latest = await client.getLatestRelease();
      final current = currentBuildOverride ??
          int.tryParse((await PackageInfo.fromPlatform()).buildNumber) ??
          0;
      if (!isUpdateAvailable(
          currentBuild: current, latestBuild: latest.build)) {
        return null;
      }
      final url = latest.downloadUrl;
      if (url == null || url.isEmpty) return null;
      return AppUpdateInfo(
        build: latest.build!,
        tag: latest.tag ?? 'apk-${latest.build}',
        downloadUrl: url,
        name: latest.name,
      );
    } catch (_) {
      return null;
    }
  }

  /// Shows the "new version available" dialog. Returns true when the
  /// user tapped Download (the URL is opened in the browser).
  static Future<bool> showUpdateDialog(
    BuildContext context,
    AppUpdateInfo info,
  ) async {
    final open = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Update available — ${info.tag}'),
        content: Text(
          'A new version of Redunator is available'
          '${info.name != null ? ' (${info.name})' : ''}. '
          'Download it to get the latest fixes and features.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Later'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Download'),
          ),
        ],
      ),
    );
    if (open == true) {
      final uri = Uri.parse(info.downloadUrl);
      if (await canLaunchUrl(uri)) {
        await launchUrl(uri, mode: LaunchMode.externalApplication);
      }
      return true;
    }
    return false;
  }

  /// Current version label for the Settings footer, e.g. "1.0.0+4".
  static Future<String> currentVersionLabel() async {
    try {
      final info = await PackageInfo.fromPlatform();
      return '${info.version}+${info.buildNumber}';
    } catch (_) {
      return '1.0.0';
    }
  }
}
