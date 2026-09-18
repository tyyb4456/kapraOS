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

export interface CreateCategoryRequest {
  name: string;
  code?: string;
  parent_id?: string;
}

export async function createCategory(data: CreateCategoryRequest): Promise<Category> {
  return apiClient.post<Category>('/products/categories', data);
}
