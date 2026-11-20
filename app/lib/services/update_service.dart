// In-app update check: compares this build (PackageInfo buildNumber,
// stamped by the GitHub Actions workflow as the run number) with the
// latest published release from GET /app/latest, and shows the update
// dialog. The download happens INSIDE the app (see [ApkDownloader]):
// the old flow handed the APK URL to the browser with launchUrl and
// then claimed "Downloading…" in Settings, but on Gabriel's phone the
// handoff silently did nothing — no browser opened, no download
// started — while the label sat there forever. Nothing here claims a
// download is happening unless bytes are actually arriving.
import 'dart:io';

import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/client.dart';
import 'apk_downloader.dart';
import 'apk_installer.dart';

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

/// What happened after the user tapped Download in the update dialog.
enum UpdateDownloadOutcome {
  /// The APK downloaded and the system installer was opened.
  downloaded,

  /// The download failed.
  failed,

  /// The user cancelled the download.
  cancelled,

  /// The in-app download failed and the user chose the browser instead.
  browserFallback,
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
  /// user tapped Download. It does NOT start any download itself —
  /// the caller runs [downloadAndInstall] so progress, errors and
  /// the install prompt are all shown honestly.
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
    return open == true;
  }

  /// Downloads [info]'s APK inside the app with a progress dialog,
  /// then opens the system installer. Returns what happened; the
  /// caller turns it into a status line (Settings does this).
  /// On failure the dialog offers Retry and "Open in browser"
  /// (the old browser handoff, kept only as a fallback).
  static Future<UpdateDownloadOutcome> downloadAndInstall(
    BuildContext context,
    AppUpdateInfo info,
  ) async {
    final downloader = ApkDownloader();
    var cancelled = false;
    var received = 0;
    int? total;
    String? error;
    File? file;
    var phase = _DownloadPhase.downloading;

    Future<void> runDownload(void Function(void Function()) setState) async {
      received = 0;
      total = null;
      error = null;
      file = null;
      phase = _DownloadPhase.downloading;
      cancelled = false;
      try {
        file = await downloader.download(
          url: info.downloadUrl,
          fileName: 'redunator-${info.tag}.apk',
          onProgress: (r, t) {
            received = r;
            total = t;
            setState(() {});
          },
          isCancelled: () => cancelled,
        );
        phase = _DownloadPhase.downloaded;
      } on ApkDownloadCancelled {
        phase = _DownloadPhase.cancelled;
      } on ApkDownloadException catch (e) {
        error = e.message;
        phase = _DownloadPhase.failed;
      } catch (_) {
        error = 'The download failed for an unknown reason.';
        phase = _DownloadPhase.failed;
      }
      setState(() {});
    }

    final outcome = await showDialog<UpdateDownloadOutcome>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        var started = false;
        return StatefulBuilder(
          builder: (ctx, setState) {
            if (!started) {
              started = true;
              // Kick off after the first frame so setState is safe.
              WidgetsBinding.instance.addPostFrameCallback(
                (_) => runDownload(setState),
              );
            }
            return AlertDialog(
              title: Text(switch (phase) {
                _DownloadPhase.downloading =>
                  'Downloading ${info.tag}…',
                _DownloadPhase.downloaded => 'Downloaded ${info.tag}',
                _DownloadPhase.failed => 'Download failed',
                _DownloadPhase.cancelled => 'Download cancelled',
              }),
              content: switch (phase) {
                _DownloadPhase.downloading => Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      LinearProgressIndicator(
                        value: total != null && total! > 0
                            ? received / total!
                            : null,
                      ),
                      const SizedBox(height: 12),
                      Text(_progressText(received, total)),
                    ],
                  ),
                _DownloadPhase.downloaded => const Text(
                    'Tap Install to update. Your phone may ask once '
                    'to allow installs from Redunator — that is the '
                    'normal prompt for installing an app outside the '
                    'Play Store.',
                  ),
                _DownloadPhase.failed => Text(
                    error ?? 'The download failed. Check your '
                        'connection and try again, or open the release '
                        'in your browser instead.',
                  ),
                _DownloadPhase.cancelled =>
                  const Text('The download was cancelled.'),
              },
              actions: switch (phase) {
                _DownloadPhase.downloading => [
                    TextButton(
                      onPressed: () {
                        cancelled = true;
                        downloader.close();
                        Navigator.of(ctx).pop(
                          UpdateDownloadOutcome.cancelled,
                        );
                      },
                      child: const Text('Cancel'),
                    ),
                  ],
                _DownloadPhase.downloaded => [
                    TextButton(
                      onPressed: () => Navigator.of(ctx).pop(
                        UpdateDownloadOutcome.cancelled,
                      ),
                      child: const Text('Later'),
                    ),
                    FilledButton(
                      onPressed: () async {
                        final f = file;
                        if (f == null) return;
                        final message =
                            await ApkInstaller().installApk(f.path);
                        if (!ctx.mounted) return;
                        if (message == 'ok') {
                          Navigator.of(ctx).pop(
                            UpdateDownloadOutcome.downloaded,
                          );
                        } else {
                          setState(() {
                            error = '$message Try opening the release '
                                'in your browser instead.';
                            phase = _DownloadPhase.failed;
                          });
                        }
                      },
                      child: const Text('Install'),
                    ),
                  ],
                _DownloadPhase.failed => [
                    TextButton(
                      onPressed: () => Navigator.of(ctx).pop(
                        UpdateDownloadOutcome.failed,
                      ),
                      child: const Text('Close'),
                    ),
                    TextButton(
                      onPressed: () async {
                        final uri = Uri.parse(info.downloadUrl);
                        if (await canLaunchUrl(uri)) {
                          await launchUrl(
                            uri,
                            mode: LaunchMode.externalApplication,
                          );
                        }
                        if (!ctx.mounted) return;
                        Navigator.of(ctx).pop(
                          UpdateDownloadOutcome.browserFallback,
                        );
                      },
                      child: const Text('Open in browser'),
                    ),
                    FilledButton(
                      onPressed: () => runDownload(setState),
                      child: const Text('Retry'),
                    ),
                  ],
                _DownloadPhase.cancelled => [
                    TextButton(
                      onPressed: () => Navigator.of(ctx).pop(
                        UpdateDownloadOutcome.cancelled,
                      ),
                      child: const Text('Close'),
                    ),
                  ],
              },
            );
          },
        );
      },
    );
    return outcome ?? UpdateDownloadOutcome.cancelled;
  }

  static String _progressText(int received, int? total) {
    final got = (received / 1048576).toStringAsFixed(1);
    if (total == null) return '$got MB downloaded';
    final all = (total / 1048576).toStringAsFixed(1);
    final pct = (received / total * 100).toStringAsFixed(0);
    return '$got / $all MB  ($pct%)';
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

enum _DownloadPhase { downloading, downloaded, failed, cancelled }
