# google_mlkit_text_recognition references every script's recognizer, but the app
# only bundles Latin; the other language packs are optional dependencies.
-dontwarn com.google.mlkit.vision.text.chinese.**
-dontwarn com.google.mlkit.vision.text.devanagari.**
-dontwarn com.google.mlkit.vision.text.japanese.**
-dontwarn com.google.mlkit.vision.text.korean.**
