import unittest
from unittest.mock import patch, MagicMock
from pyrclone import RCloneWrapper
from bucket_utils import get_bucket_config, create_client
from buckets_config import BUCKET_CONFIGS, PREFIX_TO_SERVICE

class TestBucketConfigurations(unittest.TestCase):
    def test_bucket_config_consistency(self):
        """Test that all bucket configurations have required fields"""
        for bucket_name, config in BUCKET_CONFIGS.items():
            with self.subTest(bucket=bucket_name):
                self.assertIn('type', config, f"Bucket {bucket_name} missing 'type' field")
                if config['type'] == 's3':
                    self.assertIn('provider', config, f"S3 bucket {bucket_name} missing 'provider' field")
                self.assertIn('anonymous', config, f"Bucket {bucket_name} missing 'anonymous' field")

    def test_prefix_mapping_consistency(self):
        """Test that all prefix mappings point to valid bucket configurations"""
        for prefix, service in PREFIX_TO_SERVICE.items():
            with self.subTest(prefix=prefix):
                self.assertIn(service, BUCKET_CONFIGS, 
                    f"Prefix '{prefix}' maps to non-existent bucket configuration '{service}'")

    def test_bucket_url_resolution(self):
        """Test that URLs are correctly mapped to bucket configurations"""
        test_cases = [
            ('s3://mybucket/file.txt', 's3'),
            ('b2://mybucket/file.txt', 'b2'),
            ('gs://mybucket/file.txt', 'google_cloud_storage'),
            ('ipfs://Qm123456/file.txt', 'ipfs'),
            ('sia://mybucket/file.txt', 'sia'),
            ('storj://mybucket/file.txt', 'storj'),
        ]

        for url, expected_service in test_cases:
            with self.subTest(url=url):
                config = get_bucket_config(bucket_flag='', url=url)
                self.assertEqual(config['type'], BUCKET_CONFIGS[expected_service]['type'])

    @patch('pyrclone.RCloneWrapper')
    def test_rclone_client_creation(self, mock_rclone):
        """Test that RClone client is created with correct configuration"""
        mock_instance = MagicMock()
        mock_rclone.return_value = mock_instance

        # Test with a few different bucket types
        test_configs = [
            BUCKET_CONFIGS['s3'],
            BUCKET_CONFIGS['b2'],
            BUCKET_CONFIGS['ipfs'],
            BUCKET_CONFIGS['storj'],
        ]

        for config in test_configs:
            with self.subTest(bucket_type=config['type']):
                client = create_client(config)
                self.assertIsNotNone(client)
                mock_rclone.assert_called_with({'temp_bucket': config})

    def test_invalid_bucket_flag(self):
        """Test handling of invalid bucket flag"""
        with self.assertRaises(ValueError):
            get_bucket_config('invalid_bucket', 'https://example.com')

    def test_invalid_url_format(self):
        """Test handling of invalid URL format"""
        with self.assertRaises(ValueError):
            get_bucket_config('', 'not_a_valid_url')

    def test_anonymous_access_configuration(self):
        """Test that anonymous access is properly configured"""
        for bucket_name, config in BUCKET_CONFIGS.items():
            with self.subTest(bucket=bucket_name):
                if config.get('anonymous', False):
                    # For anonymous buckets, check that no credentials are required
                    self.assertFalse('user' in config or 'password' in config or 
                                   'token' in config or 'api_key' in config,
                                   f"Anonymous bucket {bucket_name} should not have credentials")

if __name__ == '__main__':
    unittest.main()