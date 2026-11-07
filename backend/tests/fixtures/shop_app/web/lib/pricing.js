export class Pricing {
  price(item) {
    return item.qty > 0 ? item.qty * 10 : 0;
  }
}
