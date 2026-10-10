import PanelScreen from "./screens/PanelScreen";
import { mockData, mockLayout, mockValues } from "./lib/mockLayout";

export default function App() {
  return <PanelScreen layout={mockLayout} data={mockData} initialValues={mockValues} />;
}
