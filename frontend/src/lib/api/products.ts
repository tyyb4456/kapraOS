import { apiClient } from './client.ts';
import type { Product, Category } from '../../types/index.ts';

export async function getProducts(params?: { category_id?: string; search?: string }): Promise<Product[]> {
  return apiClient.get<Product[]>('/products', { params });
}

export async function getCategories(): Promise<Category[]> {
  return apiClient.get<Category[]>('/products/categories');
}

export interface CreateProductRequest {
  name: string;
  code?: string;
  category_id?: string;
  unit: 'meters' | 'yards' | 'pieces' | 'sets' | 'rolls';
  description?: string;
  variants: Array<{
    sku: string;
    barcode?: string;
    purchase_price: number;
    selling_price: number;
    unit: string;
    attributes?: Record<string, string>;
  }>;
}

export async function createProduct(data: CreateProductRequest): Promise<Product> {
  return apiClient.post<Product>('/products', data);
}

export interface UpdateProductRequest {
  name?: string;
  code?: string | null;
  product_type?: string;
  description?: string | null;
  category_id?: string;
  brand_id?: string | null;
  unit?: string;
}

export async function updateProduct(productId: string, data: UpdateProductRequest): Promise<Product> {
  return apiClient.patch<Product>(`/products/${productId}`, data);
}

export async function deleteProduct(productId: string, force = false): Promise<void> {
  await apiClient.delete(`/products/${productId}`, force ? { params: { force: true } } : undefined);
}

export interface UpdateVariantRequest {
  selling_price?: number;
  purchase_price?: number;
  is_active?: boolean;
  sku?: string;
  barcode?: string | null;
  unit?: string;
}

export async function updateVariant(variantId: string, data: UpdateVariantRequest): Promise<unknown> {
  return apiClient.patch(`/products/variants/${variantId}`, data);
}

export async function deleteVariant(variantId: string, force = false): Promise<void> {
  await apiClient.delete(`/products/variants/${variantId}`, force ? { params: { force: true } } : undefined);
}

export interface CreateCategoryRequest {
  name: string;
  code?: string;
  parent_id?: string;
}

export async function createCategory(data: CreateCategoryRequest): Promise<Category> {
  return apiClient.post<Category>('/products/categories', data);
}

export async function updateCategory(categoryId: string, data: { name?: string; parent_id?: string | null }): Promise<Category> {
  return apiClient.patch<Category>(`/products/categories/${categoryId}`, data);
}

export async function deleteCategory(categoryId: string, force = false): Promise<void> {
  await apiClient.delete(`/products/categories/${categoryId}`, force ? { params: { force: true } } : undefined);
}

export async function getBrands(): Promise<Array<{ id: string; name: string }>> {
  return apiClient.get('/products/brands');
}

export async function updateBrand(brandId: string, data: { name: string }): Promise<unknown> {
  return apiClient.patch(`/products/brands/${brandId}`, data);
}

export async function deleteBrand(brandId: string): Promise<void> {
  await apiClient.delete(`/products/brands/${brandId}`);
}
