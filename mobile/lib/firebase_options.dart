// File generated from google-services.json / FlutterFire configure.
// Run `dart pub global run flutterfire_cli:flutterfire configure` to regenerate.

import 'package:firebase_core/firebase_core.dart' show FirebaseOptions;
import 'package:flutter/foundation.dart' show defaultTargetPlatform, TargetPlatform;

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return android;
      case TargetPlatform.iOS:
        if (ios.apiKey.startsWith('YOUR_')) {
          throw UnsupportedError(
            'iOS Firebase is not configured. Run: flutterfire configure',
          );
        }
        return ios;
      case TargetPlatform.windows:
        return windows;
      default:
        throw UnsupportedError(
          'DefaultFirebaseOptions are not supported for this platform.',
        );
    }
  }

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: 'AIzaSyBoIhHQQq2-xDleiYaWaKY7u5d_c0R3vNw',
    appId: '1:683320997088:android:d0a43c4aaf159ea0b94c85',
    messagingSenderId: '683320997088',
    projectId: 'livetranslate-488616',
    storageBucket: 'livetranslate-488616.firebasestorage.app',
  );

  static const FirebaseOptions ios = FirebaseOptions(
    apiKey: 'YOUR_IOS_API_KEY',
    appId: 'YOUR_IOS_APP_ID',
    messagingSenderId: '683320997088',
    projectId: 'livetranslate-488616',
    storageBucket: 'livetranslate-488616.firebasestorage.app',
    iosBundleId: 'app.livetranslate.liveTranslateMobile',
  );

  // Windows uses the same Firebase project credentials
  static const FirebaseOptions windows = FirebaseOptions(
    apiKey: 'AIzaSyBoIhHQQq2-xDleiYaWaKY7u5d_c0R3vNw',
    appId: '1:683320997088:android:d0a43c4aaf159ea0b94c85',
    messagingSenderId: '683320997088',
    projectId: 'livetranslate-488616',
    storageBucket: 'livetranslate-488616.firebasestorage.app',
  );
}
