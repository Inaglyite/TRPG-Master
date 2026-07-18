declare module "@3d-dice/dice-box-threejs" {
  /** 最小类型声明：只覆盖我们使用到的 API。 */
  export default class DiceBox {
    constructor(selector: string, config?: Record<string, unknown>);
    initialize(): Promise<void>;
    roll(notation: string): Promise<unknown>;
    clearDice(): void;
    renderer?: {
      dispose?: () => void;
      forceContextLoss?: () => void;
      domElement?: HTMLCanvasElement;
    };
  }
}
