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
    cost_price: number;
    selling_price: number;
    attributes?: Record<string, string>;
  }>;
}

export async function createProduct(data: CreateProductRequest): Promise<Product> {
  return apiClient.post<Product>('/products', data);
}
