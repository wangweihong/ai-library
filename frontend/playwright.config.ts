import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1,
  use: {
    baseURL: process.env.AI_LIBRARY_TEST_URL || 'http://127.0.0.1:8765',
    colorScheme: 'dark',
    viewport: { width: 1512, height: 982 }, trace: 'retain-on-failure',
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
})
