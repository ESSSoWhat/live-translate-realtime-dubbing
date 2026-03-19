import React, { type FC, useState } from 'react';
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
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [apiKeyData, setApiKeyData] = useState<ApiKeyData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleGetApiKey = async () => {
    if (!email || !email.includes('@')) {
      setError('Please enter a valid email address');
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Sync member tier first
      await syncMemberTier({ email, tier: 'free' });

      // Then get/create API key
      const result = await getApiKey(email);

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

  const handleCopyApiKey = () => {
    dashboard.showToast({
      message: 'API key copied to clipboard!',
      type: 'success',
    });
  };

  const handleReset = () => {
    setApiKeyData(null);
    setEmail('');
    setError(null);
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
                <Card.Header
                  title="Your API Key"
                  subtitle="Use this key in the Live Translate desktop app"
                />
                <Card.Divider />
                <Card.Content>
                  {loading ? (
                    <Box align="center" padding="SP6">
                      <Loader size="medium" />
                      <Text secondary>Fetching your API key...</Text>
                    </Box>
                  ) : apiKeyData ? (
                    <Box direction="vertical" gap="SP4">
                      <FormField label="Email">
                        <Text>{email}</Text>
                      </FormField>
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
                      <Box marginTop="SP4">
                        <Button onClick={handleReset} skin="light" size="small">
                          Use Different Email
                        </Button>
                      </Box>
                    </Box>
                  ) : (
                    <Box direction="vertical" gap="SP4">
                      <Text>
                        Enter the email address associated with your Live Translate account to retrieve your API key.
                      </Text>
                      {error && (
                        <Text skin="error">{error}</Text>
                      )}
                      <FormField label="Email Address" required>
                        <Input
                          value={email}
                          onChange={(e) => setEmail(e.target.value)}
                          placeholder="your@email.com"
                          type="email"
                          onEnterPressed={handleGetApiKey}
                        />
                      </FormField>
                      <Box>
                        <Button onClick={handleGetApiKey} disabled={!email}>
                          Get API Key
                        </Button>
                      </Box>
                    </Box>
                  )}
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
