import Foundation
import UIKit

class ViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
    }
    
    func setupUI() {
        // Setup UI
    }
    
    func handleTap(_ sender: UIButton) {
        print("Tapped")
    }
}

struct User {
    let name: String
    let age: Int
    
    func greet() -> String {
        return "Hello, \(name)"
    }
}

func main() {
    print("Swift test")
}
