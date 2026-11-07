const express = require('express');
const { addItem, cartTotal } = require('./lib/cart');
const app = express();

app.get('/cart/:id', (req, res) => {
  res.json(cartTotal(req.params.id));
});

app.post('/cart', function (req, res) {
  res.json(addItem(req.body));
});

module.exports = app;
