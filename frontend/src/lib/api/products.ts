import { apiClient } from './client.ts';
import type { Product, Category } from '../../types/index.ts';

export async function getProducts(params?: { category_id?: string; search?: string }): Promise<Product[]> {
  return apiClient.get<Product[]>('/products', { params });
}

export async function getCategories(): Promise<Category[]> {
  return apiClient.get<Category[]>('/products/categories');
}
