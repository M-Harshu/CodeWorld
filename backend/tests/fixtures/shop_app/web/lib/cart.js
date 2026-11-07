import { Pricing } from './pricing.js';

const pricing = new Pricing();

export function addItem(item) {
  // TODO: validate item
  return pricing.price(item);
}

export function cartTotal(id) {
  return addItem({ id, qty: 1 });
}
