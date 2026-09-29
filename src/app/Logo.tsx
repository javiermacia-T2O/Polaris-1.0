import logo from "../assets/polaris-logo.png";

/** Polaris constellation mark, rendered from the generated brand asset. */
export function Logo({ size = 34 }: { size?: number }) {
  return <img src={logo} width={size} height={size} alt="Polaris"
    style={{ display: "block", objectFit: "contain" }} />;
}