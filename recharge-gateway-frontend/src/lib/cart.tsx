"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { CART_KEY } from "@/config/brand";

export type CartItem = {
  productId: number;
  slug: string;
  name: string;
  subtitle: string;
  variantId: number | null;
  specLabel: string;
  price: number;
  originalPrice: number | null;
  logoKey: string;
  brand: string;
  deliveryType: string;
  quantity: number;
};

type CartContextValue = {
  items: CartItem[];
  hydrated: boolean;
  count: number;
  subtotal: number;
  addItem: (item: Omit<CartItem, "quantity">, quantity?: number) => void;
  removeItem: (productId: number, variantId: number | null) => void;
  setQuantity: (productId: number, variantId: number | null, quantity: number) => void;
  clear: () => void;
  notice: string | null;
  clearNotice: () => void;
};

const CartContext = createContext<CartContextValue | null>(null);

function keyOf(productId: number, variantId: number | null): string {
  return `${productId}:${variantId ?? "base"}`;
}

function readCart(): CartItem[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(CART_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item.productId === "number" && typeof item.price === "number")
      .map((item) => ({ ...item, quantity: Math.min(5, Math.max(1, Number(item.quantity) || 1)) }));
  } catch {
    return [];
  }
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<CartItem[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setItems(readCart());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem(CART_KEY, JSON.stringify(items));
  }, [items, hydrated]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const addItem = useCallback((item: Omit<CartItem, "quantity">, quantity = 1) => {
    setItems((current) => {
      const existing = current.find((row) => keyOf(row.productId, row.variantId) === keyOf(item.productId, item.variantId));
      if (existing) {
        return current.map((row) =>
          keyOf(row.productId, row.variantId) === keyOf(item.productId, item.variantId)
            ? { ...row, quantity: Math.min(5, row.quantity + quantity) }
            : row,
        );
      }
      return [...current, { ...item, quantity: Math.min(5, Math.max(1, quantity)) }];
    });
    setNotice("已加入购物车");
  }, []);

  const removeItem = useCallback((productId: number, variantId: number | null) => {
    setItems((current) => current.filter((row) => keyOf(row.productId, row.variantId) !== keyOf(productId, variantId)));
  }, []);

  const setQuantity = useCallback((productId: number, variantId: number | null, quantity: number) => {
    setItems((current) =>
      current.map((row) =>
        keyOf(row.productId, row.variantId) === keyOf(productId, variantId)
          ? { ...row, quantity: Math.min(5, Math.max(1, quantity)) }
          : row,
      ),
    );
  }, []);

  const clear = useCallback(() => setItems([]), []);

  const value = useMemo<CartContextValue>(() => {
    const count = items.reduce((sum, item) => sum + item.quantity, 0);
    const subtotal = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
    return {
      items,
      hydrated,
      count,
      subtotal,
      addItem,
      removeItem,
      setQuantity,
      clear,
      notice,
      clearNotice: () => setNotice(null),
    };
  }, [items, hydrated, addItem, removeItem, setQuantity, clear, notice]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const context = useContext(CartContext);
  if (!context) throw new Error("useCart must be used inside CartProvider");
  return context;
}
