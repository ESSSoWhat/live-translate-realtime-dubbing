import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:live_translate_mobile/app.dart';

void main() {
  testWidgets('App builds', (tester) async {
    await tester.pumpWidget(const LiveTranslateApp());
    // Use pump instead of pumpAndSettle because CircularProgressIndicator
    // has continuous animation during auth check
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
