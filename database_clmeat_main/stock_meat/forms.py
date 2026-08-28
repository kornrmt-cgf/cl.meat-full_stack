from django import forms

from .models import (
    Product_info,
    Product_list,
    Category,
    meat_parts,
)


class ProductInfoForm(forms.ModelForm):

    class Meta:

        model = Product_info

        fields = [
            'type_product',
            'import_from',
            'name',
            'weight',
            'cost',
            'selling_price_per_kg',
        ]

        labels = {

            'type_product':
                'ประเภทสินค้า',

            'import_from':
                'แหล่งนำเข้า',

            'name':
                'ส่วนของเนื้อ',

            'weight':
                'น้ำหนักตั้งต้น (กรัม)',

            'cost':
                'ต้นทุน (บาท/kg)',

            'selling_price_per_kg':
                'ราคาขาย (บาท/kg)',
        }

        widgets = {

            'type_product':
                forms.Select(
                    attrs={
                        'class': 'form-control',
                        'id': 'id_type_product',
                    }
                ),

            'import_from':
                forms.Select(
                    attrs={
                        'class':
                            'form-control'
                    }
                ),

            'name':
                forms.Select(
                    attrs={
                        'class':
                            'form-control',
                        'id':
                            'id_name',
                    }
                ),

            'weight':
                forms.NumberInput(
                    attrs={
                        'class':
                            'form-control',
                        'step':
                            '0.01',
                        'min':
                            '0',
                        'placeholder':
                            'น้ำหนักกรัม'
                    }
                ),

            'cost':
                forms.NumberInput(
                    attrs={
                        'class':
                            'form-control',
                        'step':
                            '0.01',
                        'min':
                            '0',
                        'placeholder':
                            'ต้นทุนบาท/kg',
                        'id':
                            'id_cost'
                    }
                ),

            'selling_price_per_kg':
                forms.NumberInput(
                    attrs={
                        'class':
                            'form-control',
                        'step':
                            '0.01',
                        'min':
                            '0',
                        'placeholder':
                            'ราคาขายบาท/kg',
                        'id':
                            'id_selling_price_per_kg'
                    }
                ),
        }

    def __init__(self, *args, **kwargs):

        super().__init__(
            *args,
            **kwargs
        )

        # ---------------------------------------------
        # ตอนเปิดหน้าเว็บครั้งแรก
        # ไม่ต้องแสดง meat_parts ทั้งหมด
        # ---------------------------------------------

        self.fields['name'].queryset = (
            meat_parts.objects.none()
        )

        # ---------------------------------------------
        # ถ้า form ถูกส่งกลับมาเพราะ validation error
        # ให้แสดงเฉพาะเนื้อของประเภทที่เลือก
        # ---------------------------------------------

        if 'type_product' in self.data:

            try:

                category_id = int(
                    self.data.get(
                        'type_product'
                    )
                )

                self.fields['name'].queryset = (
                    meat_parts.objects
                    .filter(
                        category_id=category_id
                    )
                    .order_by('name')
                )

            except (
                ValueError,
                TypeError
            ):

                pass

        # ---------------------------------------------
        # กรณีแก้ไข Product_info เดิม
        # ---------------------------------------------

        elif self.instance.pk:

            self.fields['name'].queryset = (
                meat_parts.objects
                .filter(
                    category=self.instance.type_product
                )
                .order_by('name')
            )


class ProductListForm(forms.ModelForm):

    class Meta:

        model = Product_list

        fields = [
            'barcode',
            'weight',
            'activated',
        ]

        labels = {

            'barcode':
                'Barcode',

            'weight':
                'น้ำหนัก',

            'activated':
                'เปิดใช้งาน',
        }

        widgets = {

            'barcode':
                forms.TextInput(
                    attrs={
                        'class':
                            'form-control',

                        'placeholder':
                            'Barcode'
                    }
                ),

            'weight':
                forms.NumberInput(
                    attrs={
                        'class':
                            'form-control',

                        'step':
                            '0.01',

                        'placeholder':
                            'น้ำหนักกรัม'
                    }
                ),

            'activated':
                forms.CheckboxInput(
                    attrs={
                        'class':
                            'form-checkbox'
                    }
                ),
        }