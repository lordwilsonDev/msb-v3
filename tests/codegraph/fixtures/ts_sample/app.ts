import { helper } from "./lib";

interface Config {
  name: string;
}

function greet(name: string): string {
  return "hello " + name;
}

const add = (a: number, b: number): number => a + b;

class Greeter {
  name: string;
  constructor(name: string) {
    this.name = name;
  }
  greet(): string {
    return greet(this.name);
  }
}

export function run(): void {
  const g = new Greeter("world");
  helper(g.greet(), add(1, 2));
}
