library(ggplot2)
library(dplyr)

user_class <- setRefClass("User",
  fields = list(
    name = "character",
    age = "numeric"
  ),
  methods = list(
    greet = function() {
      paste("Hello,", name)
    }
  )
)

process_data <- function(data) {
  data %>%
    filter(value > 0) %>%
    summarize(mean_val = mean(value))
}

main <- function() {
  user <- user_class$new(name = "Alice", age = 30)
  print(user$greet())
}

main()
