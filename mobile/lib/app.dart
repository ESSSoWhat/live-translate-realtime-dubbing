import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:live_translate_mobile/screens/home_screen.dart';
import 'package:live_translate_mobile/screens/login_screen.dart';
import 'package:live_translate_mobile/services/auth_service.dart';
import 'package:live_translate_mobile/services/qonversion_service.dart';

/// Brand colors sampled from the Live Translate launcher logo (cyan on navy Earth).
abstract final class AppBrand {
  static const cyan = Color(0xFF68F8F8);
  static const cyanDeep = Color(0xFF00C4FC);
  static const navy = Color(0xFF000810);
  static const navySurface = Color(0xFF001020);
  static const navyCard = Color(0xFF001830);
  static const navyElevated = Color(0xFF002040);
  static const onCyan = Color(0xFF000810);
  static const onNavy = Color(0xFFE8F7FF);
  static const muted = Color(0xFF8AA8B8);
  static const outline = Color(0xFF2A4A60);
}

ThemeData _logoTheme() {
  final scheme = ColorScheme(
    brightness: Brightness.dark,
    primary: AppBrand.cyan,
    onPrimary: AppBrand.onCyan,
    primaryContainer: AppBrand.navyElevated,
    onPrimaryContainer: AppBrand.cyan,
    secondary: AppBrand.cyanDeep,
    onSecondary: AppBrand.onCyan,
    secondaryContainer: const Color(0xFF003058),
    onSecondaryContainer: AppBrand.cyan,
    tertiary: const Color(0xFF2CD4F8),
    onTertiary: AppBrand.onCyan,
    error: const Color(0xFFFF6B6B),
    onError: AppBrand.navy,
    errorContainer: const Color(0xFF5C1A1A),
    onErrorContainer: const Color(0xFFFFB4B4),
    surface: AppBrand.navySurface,
    onSurface: AppBrand.onNavy,
    onSurfaceVariant: AppBrand.muted,
    surfaceContainerHighest: AppBrand.navyElevated,
    surfaceContainerHigh: AppBrand.navyCard,
    surfaceContainer: AppBrand.navyCard,
    surfaceContainerLow: AppBrand.navySurface,
    surfaceContainerLowest: AppBrand.navy,
    outline: AppBrand.outline,
    outlineVariant: const Color(0xFF1A3040),
    shadow: Colors.black,
    scrim: Colors.black,
    inverseSurface: AppBrand.cyan,
    onInverseSurface: AppBrand.navy,
    inversePrimary: AppBrand.cyanDeep,
    surfaceTint: AppBrand.cyan,
  );

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppBrand.navy,
    canvasColor: AppBrand.navy,
    dividerColor: AppBrand.outline,
    appBarTheme: const AppBarTheme(
      backgroundColor: AppBrand.navy,
      foregroundColor: AppBrand.cyan,
      elevation: 0,
      centerTitle: false,
      systemOverlayStyle: SystemUiOverlayStyle(
        statusBarColor: Colors.transparent,
        statusBarIconBrightness: Brightness.light,
        systemNavigationBarColor: AppBrand.navy,
        systemNavigationBarIconBrightness: Brightness.light,
      ),
    ),
    cardTheme: CardThemeData(
      color: AppBrand.navyCard,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: AppBrand.outline),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppBrand.cyan,
        foregroundColor: AppBrand.onCyan,
        disabledBackgroundColor: AppBrand.navyElevated,
        disabledForegroundColor: AppBrand.muted,
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: AppBrand.cyan,
        foregroundColor: AppBrand.onCyan,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppBrand.cyan,
        side: const BorderSide(color: AppBrand.outline),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: AppBrand.cyan),
    ),
    floatingActionButtonTheme: const FloatingActionButtonThemeData(
      backgroundColor: AppBrand.cyan,
      foregroundColor: AppBrand.onCyan,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppBrand.navyCard,
      hintStyle: const TextStyle(color: AppBrand.muted),
      labelStyle: const TextStyle(color: AppBrand.muted),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: AppBrand.outline),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: AppBrand.outline),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: AppBrand.cyan, width: 1.5),
      ),
    ),
    chipTheme: ChipThemeData(
      backgroundColor: AppBrand.navyCard,
      selectedColor: AppBrand.navyElevated,
      labelStyle: const TextStyle(color: AppBrand.onNavy),
      secondaryLabelStyle: const TextStyle(color: AppBrand.cyan),
      side: const BorderSide(color: AppBrand.outline),
    ),
    segmentedButtonTheme: SegmentedButtonThemeData(
      style: ButtonStyle(
        backgroundColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return AppBrand.navyElevated;
          }
          return AppBrand.navyCard;
        }),
        foregroundColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return AppBrand.cyan;
          }
          return AppBrand.muted;
        }),
        side: const WidgetStatePropertyAll(BorderSide(color: AppBrand.outline)),
      ),
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: AppBrand.cyan,
      linearTrackColor: AppBrand.navyElevated,
    ),
    sliderTheme: const SliderThemeData(
      activeTrackColor: AppBrand.cyan,
      thumbColor: AppBrand.cyan,
      inactiveTrackColor: AppBrand.navyElevated,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: AppBrand.navyCard,
      contentTextStyle: const TextStyle(color: AppBrand.onNavy),
      actionTextColor: AppBrand.cyan,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: AppBrand.navyCard,
      titleTextStyle: const TextStyle(
        color: AppBrand.onNavy,
        fontSize: 20,
        fontWeight: FontWeight.w600,
      ),
      contentTextStyle: const TextStyle(color: AppBrand.muted),
    ),
    listTileTheme: const ListTileThemeData(
      iconColor: AppBrand.cyan,
      textColor: AppBrand.onNavy,
    ),
    iconTheme: const IconThemeData(color: AppBrand.cyan),
    dividerTheme: const DividerThemeData(color: AppBrand.outline),
  );
}

class LiveTranslateApp extends StatelessWidget {
  const LiveTranslateApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Live Translate',
      theme: _logoTheme(),
      themeMode: ThemeMode.dark,
      home: const AuthGate(),
    );
  }
}

class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  late final Future<bool> _hasTokensFuture;

  @override
  void initState() {
    super.initState();
    _hasTokensFuture = _initAuthAndQonversion();
  }

  Future<bool> _initAuthAndQonversion() async {
    final hasTokens = await AuthService().hasTokens();
    if (hasTokens && QonversionService.isAvailable) {
      final userId = await AuthService().userId();
      if (userId != null) {
        try {
          await QonversionService.identify(userId);
        } catch (_) {
          // Qonversion errors should not block app access
        }
      }
    }
    return hasTokens;
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<bool>(
      future: _hasTokensFuture,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        if (snapshot.hasError) {
          return Scaffold(
            body: Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(
                      'Could not check sign-in status',
                      style: Theme.of(context).textTheme.titleMedium,
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 16),
                    Text(
                      snapshot.error.toString(),
                      style: Theme.of(context).textTheme.bodySmall,
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),
            ),
          );
        }
        // Optional: --dart-define=SCREENSHOT=login|home for store listing captures
        const screenshot = String.fromEnvironment('SCREENSHOT');
        if (screenshot == 'login') {
          return const LoginScreen();
        }
        if (screenshot == 'home') {
          return const HomeScreen();
        }
        if (snapshot.data == true) {
          return const HomeScreen();
        }
        return const LoginScreen();
      },
    );
  }
}
