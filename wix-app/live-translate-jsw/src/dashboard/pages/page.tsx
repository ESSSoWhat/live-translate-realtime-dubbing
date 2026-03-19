import React, { type FC, useState, useEffect } from 'react';
import { dashboard } from '@wix/dashboard';
import {
  Box,
  Button,
  Card,
  Cell,
  CopyClipboard,
  FormField,
  Input,
  Layout,
  Loader,
  Page,
  Text,
  WixDesignSystemProvider,
} from '@wix/design-system';
import '@wix/design-system/styles.global.css';
import * as Icons from '@wix/wix-ui-icons-common';
import { getApiKey, syncMemberTier } from '../../backend/sync.web';

interface ApiKeyData {
  apiKey: string;
  userId: string;
  tier: string;
}

const Index: FC = () => {
  const [loading, setLoading] = useState(true);
  const [apiKeyData, setApiKeyData] = useState<ApiKeyData | null>(null);
  const [error, setError] = useState<string | null>(null);

  // For demo purposes, using a test email - in production, get from Wix Members
  const memberEmail = 'member@example.com';

  useEffect(() => {
    const fetchApiKey = async () => {
      try {
        setLoading(true);

        // Sync member tier first
        await syncMemberTier({ email: memberEmail, tier: 'free' });

        // Then get/create API key
        const result = await getApiKey(memberEmail);

        if (result.success && result.apiKey) {
          setApiKeyData({
            apiKey: result.apiKey,
            userId: result.userId || '',
            tier: result.tier || 'free',
          });
        } else {
          setError(result.error || 'Failed to get API key');
        }
      } catch (err) {
        setError(`Error: ${err}`);
      } finally {
        setLoading(false);
      }
    };

    fetchApiKey();
  }, []);

  const handleCopyApiKey = () => {
    dashboard.showToast({
      message: 'API key copied to clipboard!',
      type: 'success',
    });
  };

  return (
    <WixDesignSystemProvider features={{ newColorsBranding: true }}>
      <Page>
        <Page.Header
          title="Live Translate"
          subtitle="Manage your API key and subscription"
          actionsBar={
            <Button
              onClick={() => window.open('https://livetranslate.net/docs', '_blank')}
              prefixIcon={<Icons.ExternalLink />}
              skin="light"
            >
              Documentation
            </Button>
          }
        />
        <Page.Content>
          <Layout>
            <Cell span={12}>
              <Card>
                <Card.Header title="Your API Key" subtitle="Use this key in the Live Translate app" />
                <Card.Divider />
                <Card.Content>
                  {loading ? (
                    <Box align="center" padding="SP6">
                      <Loader size="medium" />
                    </Box>
                  ) : error ? (
                    <Box padding="SP4">
                      <Text skin="error">{error}</Text>
                    </Box>
                  ) : apiKeyData ? (
                    <Box direction="vertical" gap="SP4">
                      <FormField label="API Key">
                        <Box gap="SP2">
                          <Box flexGrow={1}>
                            <Input
                              value={apiKeyData.apiKey}
                              readOnly
                              type="password"
                            />
                          </Box>
                          <CopyClipboard value={apiKeyData.apiKey} onCopy={handleCopyApiKey}>
                            {({ copyToClipboard }) => (
                              <Button onClick={copyToClipboard} prefixIcon={<Icons.DuplicateSmall />}>
                                Copy
                              </Button>
                            )}
                          </CopyClipboard>
                        </Box>
                      </FormField>
                      <FormField label="Current Plan">
                        <Text weight="bold" size="medium">
                          {apiKeyData.tier.charAt(0).toUpperCase() + apiKeyData.tier.slice(1)}
                        </Text>
                      </FormField>
                    </Box>
                  ) : null}
                </Card.Content>
              </Card>
            </Cell>
          </Layout>
        </Page.Content>
      </Page>
    </WixDesignSystemProvider>
  );
};

export default Index;
