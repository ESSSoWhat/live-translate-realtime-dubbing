import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:live_translate_mobile/app.dart';

void main() {
  testWidgets('App smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const LiveTranslateApp());
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
