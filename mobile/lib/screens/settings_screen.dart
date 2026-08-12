import 'package:flutter/material.dart';

import '../services/auth_service.dart';
import 'login_screen.dart';
import 'manage_plan_screen.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        children: [
          ListTile(
            title: const Text('Manage plan'),
            subtitle: const Text('View usage, upgrade or cancel'),
            leading: const Icon(Icons.workspace_premium_outlined),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const ManagePlanScreen()),
              );
            },
          ),
          const Divider(height: 1),
          ListTile(
            title: const Text('Sign out'),
            leading: const Icon(Icons.logout),
            onTap: () async {
              try {
                await AuthService().clear();
                if (!context.mounted) return;
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const LoginScreen()),
                  (_) => false,
                );
              } catch (e) {
                if (!context.mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('Sign out failed: $e')),
                );
              }
            },
          ),
        ],
      ),
    );
  }
}
