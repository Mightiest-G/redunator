// In-app APK downloader: streams a release APK to the app's own
// external files directory with progress + cancellation, so the update
// flow no longer depends on handing the URL to the browser.
//
// Background: the old flow called launchUrl(externalApplication) and
// then set a "Downloading <tag>…" label. On Gabriel's phone the handoff
// silently did nothing — no browser opened, no download started — while
// the label sat there forever, because nothing ever verified the launch
// or tracked a real download. This class does the download itself; the
// caller shows real progress and real errors.
import 'dart:async';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

/// A download failure with a short, user-facing message.
class ApkDownloadException implements Exception {
  ApkDownloadException(this.message);
  final String message;
  @override
  String toString() => 'ApkDownloadException: $message';
}

/// Thrown when the user cancels a download. Not an error: the caller
/// treats it as "cancelled" and shows no error text.
class ApkDownloadCancelled implements Exception {}

class ApkDownloader {
  ApkDownloader({
    http.Client? client,
    Future<Directory?> Function()? directoryResolver,
  })  : _client = client ?? http.Client(),
        _directoryResolver =
            directoryResolver ?? getExternalStorageDirectory;

  final http.Client _client;
  final Future<Directory?> Function() _directoryResolver;

  /// Close the underlying client. Aborts an in-flight download; the
  /// download future then completes with [ApkDownloadCancelled] when
  /// [isCancelled] is set, or an [ApkDownloadException] otherwise.
  void close() => _client.close();

  /// Downloads [url] to `<external files>/<fileName>` and returns the
  /// file. Reports `(receivedBytes, totalBytesOrNull)` per chunk;
  /// `totalBytesOrNull` is null when the server hides the length.
  /// [isCancelled] is checked before every chunk. On any failure or
  /// cancellation the partial file is deleted.
  Future<File> download({
    required String url,
    required String fileName,
    void Function(int received, int? total)? onProgress,
    bool Function()? isCancelled,
  }) async {
    final dir = await _directoryResolver();
    if (dir == null) {
      throw ApkDownloadException('No storage folder is available.');
    }
    final file = File('${dir.path}/$fileName');
    http.StreamedResponse response;
    try {
      response = await _client.send(http.Request('GET', Uri.parse(url)));
    } on http.ClientException {
      throw ApkDownloadException(
        'Could not reach the download server — check your connection '
        'and try again.',
      );
    } catch (_) {
      throw ApkDownloadException(
        'Could not reach the download server — check your connection '
        'and try again.',
      );
    }
    if (response.statusCode != 200) {
      throw ApkDownloadException(
        'The download server answered HTTP ${response.statusCode}.',
      );
    }
    final length = response.contentLength;
    final total = length != null && length >= 0 ? length : null;
    var received = 0;
    final sink = file.openWrite();
    try {
      await for (final chunk in response.stream) {
        if (isCancelled != null && isCancelled()) {
          throw ApkDownloadCancelled();
        }
        sink.add(chunk);
        received += chunk.length;
        onProgress?.call(received, total);
      }
    } on ApkDownloadCancelled {
      await sink.close();
      if (await file.exists()) await file.delete();
      rethrow;
    } catch (_) {
      await sink.close();
      if (await file.exists()) await file.delete();
      if (isCancelled != null && isCancelled()) {
        throw ApkDownloadCancelled();
      }
      throw ApkDownloadException(
        'The download stopped partway — check your connection and '
        'try again.',
      );
    }
    await sink.flush();
    await sink.close();
    return file;
  }
}
