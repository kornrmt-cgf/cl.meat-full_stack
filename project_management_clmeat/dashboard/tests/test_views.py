"""
Tests for Dashboard Views.
"""
from django.test import TestCase, Client


from django.contrib.auth.models import User


class DashboardIndexTest(TestCase):
    """Test dashboard index view."""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')
    
    def test_index_view(self):
        """Test that index view returns 200."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
    
    def test_index_template(self):
        """Test that index view uses correct template."""
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'dashboard/index.html')
