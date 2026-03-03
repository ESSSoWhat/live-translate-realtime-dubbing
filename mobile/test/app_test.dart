import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:live_translate_mobile/app.dart';

void main() {
  testWidgets('App builds', (tester) async {
    await tester.pumpWidget(const LiveTranslateApp());
    await tester.pumpAndSettle();
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
