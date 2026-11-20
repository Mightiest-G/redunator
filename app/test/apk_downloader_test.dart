// ApkDownloader tests: real bytes, progress, cancellation cleanup,
// and failure handling — the parts the old browser-handoff flow never
// had, which is why a stuck download looked identical to a working one.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:redunator/services/apk_downloader.dart';

class FakeClient extends http.BaseClient {
  FakeClient(this._handler);
  final Future<http.StreamedResponse> Function(http.BaseRequest) _handler;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      _handler(request);
}

http.StreamedResponse streamed(
  List<int> bytes, {
  int status = 200,
  List<List<int>>? chunks,
  int? contentLength,
}) {
  final parts = chunks ??
      [
        bytes.sublist(0, bytes.length ~/ 2),
        bytes.sublist(bytes.length ~/ 2),
      ];
  return http.StreamedResponse(
    Stream.fromIterable(parts),
    status,
    contentLength: contentLength ?? bytes.length,
  );
}

void main() {
  late Directory temp;

  setUp(() async {
    temp = await Directory.systemTemp.createTemp('apk-downloader-test');
  });

  tearDown(() async {
    if (await temp.exists()) await temp.delete(recursive: true);
  });

  ApkDownloader downloader(FakeClient client) =>
      ApkDownloader(client: client, directoryResolver: () async => temp);

  test('downloads all bytes and reports progress to the total', () async {
    final bytes = utf8.encode('fake-apk-bytes-0123456789' * 4);
    final progress = <(int, int?)>[];
    final client = FakeClient(
      (_) async => streamed(bytes),
    );
    final file = await downloader(client).download(
      url: 'https://example.invalid/app-release.apk',
      fileName: 'redunator-apk-99.apk',
      onProgress: (r, t) => progress.add((r, t)),
    );
    expect(await file.readAsBytes(), bytes);
    expect(progress.last, (bytes.length, bytes.length));
    expect(progress.map((p) => p.$1), isNotEmpty);
  });

  test('progress total is null when the server hides the length', () async {
    final bytes = utf8.encode('no-length-here');
    final progress = <(int, int?)>[];
    // No contentLength passed at all: http 1.6 leaves it null, which is
    // how a real server hides the length (passing -1 throws).
    final client = FakeClient(
      (_) async => http.StreamedResponse(
        Stream.fromIterable([bytes]),
        200,
      ),
    );
    await downloader(client).download(
      url: 'https://example.invalid/app-release.apk',
      fileName: 'redunator-apk-99.apk',
      onProgress: (r, t) => progress.add((r, t)),
    );
    expect(progress.last.$2, isNull);
  });

  test('non-200 response throws and leaves no file', () async {
    final client = FakeClient(
      (_) async => streamed([], status: 404),
    );
    await expectLater(
      downloader(client).download(
        url: 'https://example.invalid/app-release.apk',
        fileName: 'redunator-apk-99.apk',
      ),
      throwsA(isA<ApkDownloadException>()),
    );
    expect(await File('${temp.path}/redunator-apk-99.apk').exists(), isFalse);
  });

  test('cancellation deletes the partial file', () async {
    final bytes = utf8.encode('chunk-one-chunk-two-chunk-three');
    var checks = 0;
    final client = FakeClient(
      (_) async => streamed(
        bytes,
        chunks: [
          bytes.sublist(0, 9),
          bytes.sublist(9, 18),
          bytes.sublist(18),
        ],
      ),
    );
    await expectLater(
      downloader(client).download(
        url: 'https://example.invalid/app-release.apk',
        fileName: 'redunator-apk-99.apk',
        isCancelled: () => ++checks >= 2,
      ),
      throwsA(isA<ApkDownloadCancelled>()),
    );
    expect(await File('${temp.path}/redunator-apk-99.apk').exists(), isFalse);
  });

  test('a network failure throws a friendly error, no file left', () async {
    final client = FakeClient(
      (_) async => throw http.ClientException('boom'),
    );
    await expectLater(
      downloader(client).download(
        url: 'https://example.invalid/app-release.apk',
        fileName: 'redunator-apk-99.apk',
      ),
      throwsA(
        isA<ApkDownloadException>().having(
          (e) => e.message,
          'message',
          contains('connection'),
        ),
      ),
    );
    expect(await File('${temp.path}/redunator-apk-99.apk').exists(), isFalse);
  });
}
