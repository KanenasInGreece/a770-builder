// forth_hidden.test.mjs — the hidden variant of the forth reference exercise (Aider's polyglot benchmark,
// JavaScript track, MIT/Exercism 2021: see kit/reference/SOURCES.md). Same public interface as the exercise's
// own forth.spec.js (Forth, evaluate, .stack), but every program string and expected result here is fresh,
// written for this file, not copied from the exercise's own spec, so a model that has memorised the public
// spec cannot pass this one by memorisation alone. Uses the same shim as the public grader (../js/expect-shim.mjs)
// and imports the model's edited module with the extension node's ESM resolver requires — this file is not
// the verbatim exercise spec, so it needs no rewrite trick.
import '../js/expect-shim.mjs';
import { Forth } from '../javascript/forth/forth.js';

describe('Forth (hidden)', () => {
  let forth;

  beforeEach(() => {
    forth = new Forth();
  });

  describe('parsing and numbers', () => {
    test('a longer run of numbers just gets pushed', () => {
      forth.evaluate('10 -20 30 -40 50 60');
      expect(forth.stack).toEqual([10, -20, 30, -40, 50, 60]);
    });
  });

  describe('addition', () => {
    test('can add two numbers', () => {
      forth.evaluate('7 8 +');
      expect(forth.stack).toEqual([15]);
    });

    test('errors if there is nothing on the stack', () => {
      expect(() => {
        forth.evaluate('+');
      }).toThrow(new Error('Stack empty'));
    });

    test('errors if there is only one value on the stack', () => {
      expect(() => {
        forth.evaluate('9 +');
      }).toThrow(new Error('Stack empty'));
    });
  });

  describe('subtraction', () => {
    test('can subtract two numbers', () => {
      forth.evaluate('10 6 -');
      expect(forth.stack).toEqual([4]);
    });

    test('errors if there is only one value on the stack', () => {
      expect(() => {
        forth.evaluate('2 -');
      }).toThrow(new Error('Stack empty'));
    });
  });

  describe('multiplication', () => {
    test('can multiply two numbers', () => {
      forth.evaluate('6 7 *');
      expect(forth.stack).toEqual([42]);
    });
  });

  describe('division', () => {
    test('can divide two numbers', () => {
      forth.evaluate('20 4 /');
      expect(forth.stack).toEqual([5]);
    });

    test('performs integer division', () => {
      forth.evaluate('17 5 /');
      expect(forth.stack).toEqual([3]);
    });

    test('errors if dividing by zero', () => {
      expect(() => {
        forth.evaluate('9 0 /');
      }).toThrow(new Error('Division by zero'));
    });
  });

  describe('combined arithmetic', () => {
    test('addition then multiplication', () => {
      forth.evaluate('2 3 + 4 *');
      expect(forth.stack).toEqual([20]);
    });
  });

  describe('dup, drop, swap, over', () => {
    test('dup copies the top value', () => {
      forth.evaluate('9 dup');
      expect(forth.stack).toEqual([9, 9]);
    });

    test('drop removes the top value', () => {
      forth.evaluate('4 5 drop');
      expect(forth.stack).toEqual([4]);
    });

    test('swap exchanges the top two values', () => {
      forth.evaluate('1 2 3 swap');
      expect(forth.stack).toEqual([1, 3, 2]);
    });

    test('over copies the second element', () => {
      forth.evaluate('5 6 over');
      expect(forth.stack).toEqual([5, 6, 5]);
    });

    test('drop errors on an empty stack', () => {
      expect(() => {
        forth.evaluate('drop');
      }).toThrow(new Error('Stack empty'));
    });
  });

  describe('user-defined words', () => {
    test('can consist of built-in words', () => {
      forth.evaluate(': triple dup dup ;');
      forth.evaluate('2 triple');
      expect(forth.stack).toEqual([2, 2, 2]);
    });

    test('execute in the right order', () => {
      forth.evaluate(': three-numbers 7 8 9 ;');
      forth.evaluate('three-numbers');
      expect(forth.stack).toEqual([7, 8, 9]);
    });

    test('can override a built-in word', () => {
      forth.evaluate(': dup drop ;');
      forth.evaluate('1 2 dup');
      expect(forth.stack).toEqual([1]);
    });

    test('can override a built-in operator', () => {
      forth.evaluate(': - + ;');
      forth.evaluate('5 6 -');
      expect(forth.stack).toEqual([11]);
    });

    test('cannot redefine a number', () => {
      expect(() => {
        forth.evaluate(': 42 43 ;');
      }).toThrow(new Error('Invalid definition'));
    });

    test('errors executing a non-existent word', () => {
      expect(() => {
        forth.evaluate('nosuchword');
      }).toThrow(new Error('Unknown command'));
    });

    test('two evaluators do not share definitions', () => {
      const a = new Forth();
      const b = new Forth();
      a.evaluate(': + - ;');
      a.evaluate('5 2 +');
      b.evaluate('5 2 +');
      expect(a.stack).toEqual([3]);
      expect(b.stack).toEqual([7]);
    });
  });

  describe('case-insensitivity', () => {
    test('built-in words are case-insensitive', () => {
      forth.evaluate('3 DUP Dup dup');
      expect(forth.stack).toEqual([3, 3, 3, 3]);
    });

    test('user-defined words are case-insensitive', () => {
      forth.evaluate(': foo dup ;');
      forth.evaluate('2 FOO Foo foo');
      expect(forth.stack).toEqual([2, 2, 2, 2]);
    });
  });
});
