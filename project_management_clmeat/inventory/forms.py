"""
Forms for Inventory management.
"""
from django import forms
from .models import Product, Batch, Package, StorageLocation


class ProductForm(forms.ModelForm):
    """Form for creating/editing products."""
    
    class Meta:
        model = Product
        fields = ['sku', 'barcode', 'name', 'category', 'unit', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'sku': forms.TextInput(attrs={'class': 'form-control'}),
            'barcode': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class BatchForm(forms.ModelForm):
    """Form for creating batches."""
    
    class Meta:
        model = Batch
        fields = ['batch_number', 'supplier', 'received_at', 'notes', 'active']
        widgets = {
            'batch_number': forms.TextInput(attrs={'class': 'form-control'}),
            'supplier': forms.TextInput(attrs={'class': 'form-control'}),
            'received_at': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class PackageForm(forms.ModelForm):
    """Form for creating packages."""
    
    class Meta:
        model = Package
        fields = ['product', 'batch', 'barcode', 'weight', 'packed_at', 'storage_location']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'batch': forms.Select(attrs={'class': 'form-select'}),
            'barcode': forms.TextInput(attrs={'class': 'form-control'}),
            'weight': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'packed_at': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'storage_location': forms.Select(attrs={'class': 'form-select'}),
        }


class StorageLocationForm(forms.ModelForm):
    """Form for creating storage locations."""
    
    class Meta:
        model = StorageLocation
        fields = ['name', 'location_type', 'capacity', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'location_type': forms.Select(attrs={'class': 'form-select'}),
            'capacity': forms.NumberInput(attrs={'class': 'form-control'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
