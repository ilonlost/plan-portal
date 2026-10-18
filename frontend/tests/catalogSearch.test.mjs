import { test } from "node:test";
import assert from "node:assert/strict";
import { matchesCatalogText, normalizeCatalogSearch } from "../src/catalogSearch.ts";

const article = "1010030855 Маффин шоколадный Булка";

test("lookup and table agree on pasted SKU with surrounding whitespace", () => {
  for (const query of ["1010030855", " 1010030855", "1010030855  ", "\t1010030855\n", "\u00a01010030855\u00a0", "\u200b1010030855\ufeff"]) {
    assert.equal(normalizeCatalogSearch(query), "1010030855");
    assert.equal(matchesCatalogText(article, query), true, JSON.stringify(query));
  }
});

test("visually empty saved column filters do not hide articles", () => {
  for (const filter of ["", " ", "\u00a0", "\u200b"]) {
    assert.equal(matchesCatalogText("По графику", filter), true);
  }
});

test("name search ignores case and repeated spaces while real filters still apply", () => {
  assert.equal(matchesCatalogText(article, "МАФФИН   ШОКОЛАДНЫЙ"), true);
  assert.equal(matchesCatalogText(article, "1010035972"), false);
  assert.equal(matchesCatalogText("По графику", "АЗ"), false);
});
